#!/usr/bin/env python3
"""
SPIRE-Net fine-tuning script.

SCOPE
=====
This is a *short* fine-tuning pass over the shipped SPIRE-Net weights
(`spire_net_weights.npz`). It generates a small set of synthetic
training samples via `synthetic_hq`, fits a cylindrical map per
sample, runs the SGD step from `nn_grs`, and writes the result to
**a separate file** (`spire_net_finetuned.npz`). The shipped weights
are never overwritten.

Why separate?
    - The shipped weights are the canonical, reproducible state. A
      fine-tune that quietly overwrites them would be a debugging
      nightmare.
    - You can A/B test: run the measurement with the shipped weights
      and with the fine-tuned weights, and compare the two.
    - The fine-tune is not committed to git; it lives in your local
      `app/models/spire_net_finetuned.npz` and is gitignored.

What the fine-tune does:
    - Start from the shipped weights (or "scratch" via the --scratch
      flag).
    - Generate N synthetic samples (default 32).
    - For each sample, build a cylindrical map, target = (lon, lat)
      of the synthetic GRS in cylindrical coords.
    - Run a small number of epochs (default 6) with a small learning
      rate (default 0.003) on the full train set per epoch.
    - Save the result and a small JSON diagnostic with the loss curve.

What the fine-tune does NOT do:
    - It does not do the full overnight training (that's
      `nn_grs.overnight_train`).
    - It does not modify the published champion path. Fine-tuned
      weights are an *opt-in* soft prior.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Make sure we can import from the app directory
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from verbose_log import CONSOLE
from paths import ensure_models_present, model_dir


# -----------------------------------------------------------------------------
# Defaults — tuned for a short, robust fine-tune pass
# -----------------------------------------------------------------------------

DEFAULTS = {
    "n_samples": 32,
    "epochs": 6,
    "lr": 0.003,
    "resolution": "1080p",
    "region": "global",
    "mode": "metrology",
    "seed": 1234,
    "scratch": False,
    "out_name": "spire_net_finetuned.npz",
}


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _generate_sample(
    seed: int, resolution: str, region: str, mode: str, out_dir: Path,
) -> Optional[Tuple[np.ndarray, Tuple[float, float, float]]]:
    """
    Generate one synthetic Jupiter frame, return (cyl_map, (lon, lat, cm)).
    """
    try:
        from synthetic_hq import SynthSpec, generate
        from precision_engine import fit_limb_nav, make_cylindrical, to_mono
        import grs_complete_system as grs
    except Exception as e:
        CONSOLE.warn(f"import failed: {e}")
        return None
    try:
        spec = SynthSpec(
            user_time_iso="",
            region=region,
            resolution_preset=resolution,
            random_time=True,
            seed=int(seed),
            mode=mode,
            write_grs_crop=False,
        )
        _png, fit, truth = generate(spec, out_dir)
        arr, _ = grs.read_fits(fit)
        img = np.asarray(arr, dtype=np.float64)
        if img.ndim == 3 and img.shape[0] == 3:
            mono = 0.3 * img[0] + 0.5 * img[1] + 0.2 * img[2]
        else:
            mono = img
        nav = fit_limb_nav(mono, cm_iii_deg=truth["cm_iii_deg"],
                            distance_au=truth["distance_au"])
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        cyl = make_cylindrical(mono, nav, width=256, height=128)
        return cyl, (
            float(truth["grs_lon_iii_deg"]),
            float(truth["grs_lat_deg"]),
            float(truth["cm_iii_deg"]),
        )
    except Exception as e:
        CONSOLE.debug(f"sample generation failed: {e}")
        return None


def _to_nn_input(cyl: np.ndarray) -> np.ndarray:
    """Resize a cylindrical map to the network input size (64×128)."""
    from nn_grs import map_to_nn_input
    return map_to_nn_input(cyl)


def _truth_to_targets(lon_iii: float, lat: float, cm_iii: float):
    """Convert (lon, lat, cm) to network targets (heatmap, coords)."""
    from nn_grs import truth_to_targets
    return truth_to_targets(lon_iii, lat, cm_iii)


def _save_finetuned(net, path: Path) -> None:
    """Save the fine-tuned net to `path` (separate from the shipped weights)."""
    from nn_grs import _atomic_savez, weights_are_finite
    if not weights_are_finite(net):
        raise ValueError("Refusing to save: fine-tuned net has NaN/Inf weights.")
    arrays = dict(
        w1=net.w1, b1=net.b1, w2=net.w2, b2=net.b2, w3=net.w3, b3=net.b3,
        wf1=net.wf1, bf1=net.bf1, wf2=net.wf2, bf2=net.bf2,
        wh=net.wh, bh=net.bh, wc=net.wc, bc=net.bc,
        trained=np.array([1 if net.trained else 0]),
    )
    _atomic_savez(path, **arrays)


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------

def run_finetune(
    n_samples: int = DEFAULTS["n_samples"],
    epochs: int = DEFAULTS["epochs"],
    lr: float = DEFAULTS["lr"],
    resolution: str = DEFAULTS["resolution"],
    region: str = DEFAULTS["region"],
    mode: str = DEFAULTS["mode"],
    seed: int = DEFAULTS["seed"],
    scratch: bool = DEFAULTS["scratch"],
    out_name: str = DEFAULTS["out_name"],
) -> Dict[str, Any]:
    """Run the short fine-tune pass and return a diagnostic dict."""
    import shutil
    from nn_grs import (
        SpireNet, _sgd_step, weights_are_finite,
        snapshot_weights, restore_weights, _atomic_savez,
    )

    t0 = time.time()
    md = ensure_models_present()
    shipped = md / "spire_net_weights.npz"
    out_path = md / out_name
    # Working scratch dir for the synthetic samples
    scratch_dir = md.parent / "nn_finetune_cache"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    # Load starting net
    if scratch or not shipped.exists():
        net = SpireNet.create(seed=seed)
        started_from = "scratch"
    else:
        try:
            net = SpireNet.load(shipped)
            if net is None:
                raise RuntimeError("load returned None")
            started_from = "shipped"
        except Exception as e:
            CONSOLE.warn(f"could not load shipped weights: {e}")
            net = SpireNet.create(seed=seed)
            started_from = "scratch_after_load_fail"
    if not weights_are_finite(net):
        CONSOLE.warn("shipped weights corrupt — re-initialising")
        net = SpireNet.create(seed=seed)
        started_from = "scratch_after_corrupt"
    net.trained = True
    # Generate samples
    rng = np.random.default_rng(seed)
    samples: List[Tuple[np.ndarray, Tuple[float, float, float]]] = []
    for k in range(n_samples):
        sample_seed = int(rng.integers(1, 2 ** 30))
        s = _generate_sample(sample_seed, resolution, region, mode, scratch_dir)
        if s is not None:
            samples.append(s)
        if (k + 1) % 8 == 0 or k == n_samples - 1:
            CONSOLE.info(f"generated {len(samples)}/{n_samples} samples (tried {k + 1})")
    if not samples:
        raise RuntimeError("no synthetic samples could be generated for fine-tuning")
    # Build network inputs + targets
    inputs, targets = [], []
    for cyl, (lon, lat, cm) in samples:
        x = _to_nn_input(cyl)
        h_t, c_t = _truth_to_targets(lon, lat, cm)
        inputs.append(x)
        targets.append((h_t, c_t))
    # Training loop
    losses: List[float] = []
    snap = snapshot_weights(net)
    initial_loss = None
    for ep in range(epochs):
        ep_losses: List[float] = []
        for k in range(len(inputs)):
            x = inputs[k]
            h_t, c_t = targets[k]
            loss = _sgd_step(net, x, h_t, c_t, lr=lr)
            if loss is None or not math.isfinite(float(loss)):
                # bad step — restore and continue
                restore_weights(net, snap)
                continue
            ep_losses.append(float(loss))
        if not ep_losses:
            CONSOLE.warn(f"epoch {ep + 1}: no finite losses; restoring")
            restore_weights(net, snap)
            continue
        snap = snapshot_weights(net)
        mean_loss = float(np.mean(ep_losses))
        losses.append(mean_loss)
        if initial_loss is None:
            initial_loss = mean_loss
        CONSOLE.info(f"epoch {ep + 1}/{epochs}: loss={mean_loss:.5f}")
    final_loss = losses[-1] if losses else float("nan")
    gain = None
    if initial_loss is not None and losses:
        gain = initial_loss - final_loss
    gain_pct = None
    if (initial_loss is not None and initial_loss > 0
            and gain is not None and losses):
        gain_pct = float(100.0 * (initial_loss - final_loss) / initial_loss)
    # Save
    try:
        _save_finetuned(net, out_path)
    except Exception as e:
        CONSOLE.error(f"fine-tune save failed: {e}")
        raise
    elapsed = time.time() - t0
    # Diagnostic
    diag = {
        "started_from": started_from,
        "out_path": str(out_path),
        "n_samples": len(samples),
        "epochs": epochs,
        "lr": lr,
        "resolution": resolution,
        "region": region,
        "mode": mode,
        "loss_curve": losses,
        "initial_loss": initial_loss,
        "final_loss": final_loss,
        "improvement": gain,
        "improvement_pct": gain_pct,
        "elapsed_s": elapsed,
        "shipped_weights": str(shipped),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }
    diag_path = out_path.with_suffix(".json")
    diag_path.write_text(json.dumps(diag, indent=2, default=str), encoding="utf-8")
    CONSOLE.ok(
        f"fine-tune done: {len(samples)} samples × {epochs} epochs, "
        f"loss {initial_loss:.4f} → {final_loss:.4f} "
        f"(gain {gain_pct:+.1f}%), saved → {out_path.name}"
    )
    return diag


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fine-tune SPIRE-Net on synthetic Jupiter maps (writes spire_net_finetuned.npz)."
    )
    ap.add_argument("--n", type=int, default=DEFAULTS["n_samples"],
                    help="number of synthetic samples")
    ap.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    ap.add_argument("--lr", type=float, default=DEFAULTS["lr"])
    ap.add_argument("--res", default=DEFAULTS["resolution"])
    ap.add_argument("--region", default=DEFAULTS["region"])
    ap.add_argument("--mode", default=DEFAULTS["mode"])
    ap.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    ap.add_argument("--scratch", action="store_true",
                    help="initialise from scratch (not the shipped weights)")
    ap.add_argument("--out", default=DEFAULTS["out_name"])
    args = ap.parse_args()
    try:
        diag = run_finetune(
            n_samples=args.n,
            epochs=args.epochs,
            lr=args.lr,
            resolution=args.res,
            region=args.region,
            mode=args.mode,
            seed=args.seed,
            scratch=args.scratch,
            out_name=args.out,
        )
        print(json.dumps(diag, indent=2, default=str))
        return 0
    except Exception as e:
        CONSOLE.error(f"fine-tune failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
