#!/usr/bin/env python3
"""video_batch.py — batch SER/AVI capture streaming into the stacking queue.

WHY THIS EXISTS
===============
`observatory_pipeline.stack_video` already streams a *single* .ser/.avi
straight into the APS stacker (ser_io reads frames on demand from a
memory-mapped file — no extraction to an image folder). A night of planetary
imaging produces a *folder* of captures (one per filter / run), and re-pointing
the pipeline at each one by hand is tedious and error-prone. This module adds
the missing batch layer:

    discover_captures()   -> ordered list of .ser/.avi files
    run_video_batch()     -> stream each one through the stacker, write a
                             per-capture report + one batch summary JSON

DESIGN
======
  * Streaming, never extraction: every capture is opened with ser_io and the
    frames are yielded straight into `stack_ap` — the same path a single
    capture takes, so batch numbers equal single-run numbers.
  * Fail-per-file, not fail-the-batch: one corrupt capture produces a per-file
    error entry and the loop continues; the summary flags which files failed.
  * Optional measurement: `measure=True` runs the full video->answer path per
    capture. It only succeeds when a mid-exposure UTC is available (SER stamps
    or an explicit `--time`), otherwise that file is reported as skipped — the
    repo never fabricates a System III longitude.
  * Typed, dependency-free core; the heavy lifting stays in the modules that
    already own it (ser_io / observatory_pipeline).
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

VIDEO_EXTS = (".ser", ".avi")


def discover_captures(
    paths: Union[str, Path, Sequence[Union[str, Path]]],
    *,
    exts: Sequence[str] = VIDEO_EXTS,
    recursive: bool = False,
) -> List[Path]:
    """Collect .ser/.avi capture files from paths (a dir, glob, or list).

    A directory is scanned (recursively when `recursive`); an explicit file or
    list of files is passed through as-is. Results are de-duplicated and sorted
    by name for a reproducible run order.
    """
    exts = tuple(e.lower() if e.startswith(".") else f".{e.lower()}" for e in exts)
    found: Dict[str, Path] = {}
    if isinstance(paths, (str, Path)):
        paths = [paths]
    for p in paths:
        p = Path(p)
        if p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            for f in it:
                if f.is_file() and f.suffix.lower() in exts:
                    found[str(f.resolve())] = f
        else:
            # a glob (e.g. "*.ser") or an explicit file
            if any(ch in str(p) for ch in "*?["):
                for f in sorted(p.parent.glob(p.name)):
                    if f.is_file() and f.suffix.lower() in exts:
                        found[str(f.resolve())] = f
            elif p.is_file():
                found[str(p.resolve())] = p
    return [found[k] for k in sorted(found)]


@dataclass
class BatchItem:
    path: str
    ok: bool
    out_dir: Optional[str] = None
    error: str = ""
    report: Dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # keep the summary lean: drop the per-file full report, point at the file
        if d.get("report"):
            d["report_keys"] = sorted(d.pop("report").keys())
        return d


@dataclass
class BatchResult:
    items: List[BatchItem] = field(default_factory=list)
    out_dir: str = ""
    n_total: int = 0
    n_ok: int = 0
    n_failed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_total": self.n_total,
            "n_ok": self.n_ok,
            "n_failed": self.n_failed,
            "out_dir": self.out_dir,
            "items": [i.to_dict() for i in self.items],
        }


def _frac_dir(frac: float) -> str:
    """Filesystem-safe, unambiguous subdirectory name for a keep fraction."""
    return f"keep_{int(round(float(frac) * 100.0)):02d}pct"


def _normalize_keep_fracs(
    keep_frac: float, keep_fracs: Optional[Sequence[float]]
) -> List[float]:
    """Merge the scalar default with an explicit multi-stack list.

    AutoStakkert practice stacks at several keep fractions (5/10/20/50%);
    a single list drives that. The FIRST entry is the "primary" stack whose
    report fills the per-item report (backward-compatible).
    """
    if keep_fracs:
        fracs = [float(f) for f in keep_fracs if float(f) > 0.0]
        # de-dupe, keep first occurrence order
        seen: List[float] = []
        for f in fracs:
            if f not in seen:
                seen.append(f)
        return seen
    return [float(keep_frac)]


def run_video_batch(
    captures: Sequence[Union[str, Path]],
    *,
    out_root: Optional[Union[str, Path]] = None,
    keep_frac: float = 0.25,
    keep_fracs: Optional[Sequence[float]] = None,
    drizzle: int = 1,
    ap_size: int = 32,
    step: int = 1,
    limit: int = 0,
    downsample: int = 1,
    sharpen_method: str = "none",
    measure: bool = False,
    time_utc: Optional[str] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> BatchResult:
    """Stream every capture through the stacker (optionally + measurement).

    ``progress(done, total, label)`` is called before each capture starts, so
    the desktop app / CLI can render a determinate bar without blocking. When
    no callback is supplied, a one-line progress message is written to stderr
    (keeps the JSON summary on stdout clean for machine consumption).

    ``keep_fracs`` (e.g. ``(0.05, 0.10, 0.20, 0.50)``) produces one APS stack
    per keep fraction per capture — the AutoStakkert multi-stack practice —
    each written to its own ``keep_NNpct/`` subdirectory. The first fraction
    is the primary stack whose report fills the per-item report (so existing
    consumers of ``report["out_dir"]`` keep working). In ``measure`` mode the
    full video->answer path runs once per capture on the primary fraction.
    """
    import observatory_pipeline as op

    root = Path(out_root) if out_root else Path("outputs") / "video_batch"
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    root = root / stamp
    root.mkdir(parents=True, exist_ok=True)

    if progress is None:
        progress = lambda done, total, label: print(  # noqa: E731
            f"[{done}/{total}] {label}", file=sys.stderr)

    fracs = _normalize_keep_fracs(keep_frac, keep_fracs)

    res = BatchResult(out_dir=str(root), n_total=len(captures))
    for k, cap in enumerate(captures):
        cap = Path(cap)
        label = cap.name
        progress(k, len(captures), label)
        item = BatchItem(path=str(cap), ok=False)
        t0 = time.time()
        try:
            multi = len(fracs) > 1
            stacks: List[Dict[str, Any]] = []
            for fi, f in enumerate(fracs):
                out_sub = root / cap.stem / _frac_dir(f)
                rep = op.stack_video(
                    video_path=str(cap),
                    out_dir=out_sub,
                    keep_frac=f, drizzle=drizzle, ap_size=ap_size,
                    step=step, limit=limit, downsample=downsample,
                    sharpen_method=sharpen_method,
                )
                stacks.append({
                    "keep_frac": f,
                    "out_dir": str(out_sub),
                    "stack_png": rep.get("stack_png"),
                    "ok": True,
                    "error": "",
                })
                if fi == 0:
                    item.report = dict(rep)
                    item.out_dir = str(out_sub)
            if measure:
                # full answer once, on the primary fraction — never fabricates
                # System III (needs UTC stamps or --time; fails closed)
                ans = op.video_to_answer(
                    str(cap),
                    time_utc=time_utc or None,
                    keep_frac=fracs[0], drizzle=drizzle, ap_size=ap_size,
                    step=step, limit=limit, downsample=downsample,
                    sharpen_method=sharpen_method,
                    out_root=root / cap.stem / "measure",
                )
                if multi:
                    item.report["measurement"] = {
                        "output_dir": ans.get("out_dir"),
                        "headline": ans.get("measurement", {}).get("headline")
                        if isinstance(ans.get("measurement"), dict) else None,
                        "publish": ans.get("measurement", {}).get("publish")
                        if isinstance(ans.get("measurement"), dict) else None,
                        "time_utc": ans.get("time_utc"),
                        "note": ans.get("note"),
                    }
                    item.report["stacks"] = stacks
                    item.report["primary_keep_frac"] = fracs[0]
                else:
                    item.report = dict(ans)
                    item.out_dir = str(root / cap.stem / "measure")
            elif multi:
                item.report["stacks"] = stacks
                item.report["primary_keep_frac"] = fracs[0]
            item.ok = True
        except Exception as e:  # noqa: BLE001 - recorded per file, not swallowed
            item.error = f"{type(e).__name__}: {e}"
        finally:
            item.seconds = round(time.time() - t0, 3)
        res.items.append(item)

    res.n_ok = sum(1 for i in res.items if i.ok)
    res.n_failed = res.n_total - res.n_ok
    summary = res.to_dict()
    (root / "batch_report.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (root / "BATCH_SUMMARY.txt").write_text(
        batch_summary_text(res), encoding="utf-8")
    return res


def batch_summary_text(res: BatchResult) -> str:
    """Human-readable batch summary."""
    lines = [
        "VIDEO BATCH",
        "===========",
        f"captures      {res.n_total}",
        f"ok            {res.n_ok}",
        f"failed        {res.n_failed}",
        f"out           {res.out_dir}",
        "",
    ]
    for i in res.items:
        status = "OK  " if i.ok else "FAIL"
        lines.append(f"  [{status}] {Path(i.path).name}  "
                     f"{i.seconds}s  {i.error or (i.out_dir or '')}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python video_batch.py <dir-or-files...> [--measure] [--time UTC]")
        raise SystemExit(1)
    caps = discover_captures(sys.argv[1:])
    if not caps:
        print("no .ser/.avi captures found")
        raise SystemExit(2)
    r = run_video_batch(caps, measure="--measure" in sys.argv,
                        time_utc=sys.argv[sys.argv.index("--time") + 1]
                        if "--time" in sys.argv else None)
    print(batch_summary_text(r))
