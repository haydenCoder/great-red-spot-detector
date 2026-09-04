"""Tests for app/video_batch.py — batch SER/AVI streaming into the stacker."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import ser_io  # noqa: E402
from video_batch import (BatchItem, batch_summary_text, discover_captures,  # noqa: E402
                         run_video_batch)


def _frames(n=8, size=48):
    out = []
    for k in range(n):
        yy, xx = np.mgrid[0:size, 0:size]
        r = np.sqrt((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (size * 0.4)
        disk = np.clip(1.0 - 0.15 * r, 0, 1) * (r <= 1.0)
        disk = np.roll(disk, shift=k % 2, axis=1)
        out.append((disk * 255.0).astype(np.uint8))
    return out


class TestDiscover:
    def test_directory_scan(self, tmp_path):
        ser_io.write_ser(tmp_path / "a.ser", _frames())
        ser_io.write_ser(tmp_path / "b.ser", _frames())
        (tmp_path / "notes.txt").write_text("ignore me")
        caps = discover_captures([str(tmp_path)])
        assert [c.name for c in caps] == ["a.ser", "b.ser"]

    def test_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        ser_io.write_ser(sub / "c.ser", _frames())
        assert len(discover_captures([str(tmp_path)])) == 0
        assert len(discover_captures([str(tmp_path)], recursive=True)) == 1

    def test_glob_and_explicit_file(self, tmp_path):
        ser_io.write_ser(tmp_path / "x.ser", _frames())
        assert len(discover_captures([str(tmp_path / "*.ser")])) == 1
        assert len(discover_captures([str(tmp_path / "x.ser")])) == 1


class TestBatchRun:
    def test_two_captures_stack(self, tmp_path):
        ser_io.write_ser(tmp_path / "a.ser", _frames())
        ser_io.write_ser(tmp_path / "b.ser", _frames())
        res = run_video_batch([tmp_path / "a.ser", tmp_path / "b.ser"],
                              out_root=tmp_path / "out")
        assert res.n_total == 2
        assert res.n_ok == 2
        assert res.n_failed == 0
        for it in res.items:
            assert it.ok and it.out_dir and Path(it.out_dir).exists()
            assert (Path(it.out_dir) / "aps_stack.png").exists()
        assert (Path(res.out_dir) / "batch_report.json").exists()

    def test_batch_report_json_round_trip(self, tmp_path):
        ser_io.write_ser(tmp_path / "a.ser", _frames())
        res = run_video_batch([tmp_path / "a.ser"], out_root=tmp_path / "out")
        d = json.loads((Path(res.out_dir) / "batch_report.json").read_text())
        assert d["n_total"] == 1 and d["n_ok"] == 1
        assert d["items"][0]["ok"] is True

    def test_corrupt_file_does_not_kill_batch(self, tmp_path):
        ser_io.write_ser(tmp_path / "good.ser", _frames())
        (tmp_path / "bad.ser").write_bytes(b"LUCAM-RECORDER" + b"\x00" * 40)
        res = run_video_batch([tmp_path / "good.ser", tmp_path / "bad.ser"],
                              out_root=tmp_path / "out")
        assert res.n_total == 2
        assert res.n_ok == 1
        assert res.n_failed == 1
        assert any("bad.ser" in i.path and not i.ok and i.error for i in res.items)

    def test_summary_text(self, tmp_path):
        ser_io.write_ser(tmp_path / "a.ser", _frames())
        res = run_video_batch([tmp_path / "a.ser"], out_root=tmp_path / "out")
        txt = batch_summary_text(res)
        assert "OK" in txt and "a.ser" in txt


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
