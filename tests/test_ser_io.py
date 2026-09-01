"""Tests for ser_io — SER and uncompressed AVI video I/O (round-trip + format)."""
from __future__ import annotations

import datetime as dt
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import ser_io  # noqa: E402


def _mk_frames(n=4, h=48, w=64, dtype=np.uint8, color=False, seed=7):
    rng = np.random.default_rng(seed)
    frames = []
    for k in range(n):
        if color:
            a = rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)
        else:
            a = rng.integers(0, 255, size=(h, w), dtype=np.uint8)
        # put a frame-index "barcode" so order is verifiable
        a[2:6, 2:6] = k * 10
        frames.append(a.astype(dtype) if dtype == np.uint8 else
                      (a.astype(np.uint16) * 257 if dtype == np.uint16 else a))
    return frames


class TestTicks(unittest.TestCase):
    def test_roundtrip(self):
        t = dt.datetime(2026, 8, 1, 3, 4, 5, tzinfo=dt.timezone.utc)
        ticks = ser_io.datetime_to_ticks(t)
        back = ser_io.ticks_to_datetime(ticks)
        self.assertIsNotNone(back)
        self.assertLess(abs((back - t).total_seconds()), 1.0)

    def test_zero_is_none(self):
        self.assertIsNone(ser_io.ticks_to_datetime(0))


class TestSERRoundTrip(unittest.TestCase):
    def test_mono8(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cap.ser"
            frames = _mk_frames(5)
            times = [dt.datetime(2026, 8, 1, 0, 0, 10 * k, tzinfo=dt.timezone.utc) for k in range(5)]
            ser_io.write_ser(p, frames, observer="OB", instrument="IM", telescope="TE",
                             frame_times_utc=times)
            v = ser_io.read_video(p)
            self.assertEqual(v.meta.container, "ser")
            self.assertEqual(v.meta.n_frames, 5)
            self.assertEqual((v.meta.width, v.meta.height), (64, 48))
            self.assertEqual(v.meta.observer, "OB")
            self.assertEqual(v.meta.instrument, "IM")
            self.assertEqual(v.meta.telescope, "TE")
            for k, f in enumerate(frames):
                got = v.frame_raw(k)
                np.testing.assert_array_equal(got, f)
                t = v.frame_utc(k)
                self.assertIsNotNone(t)
                self.assertLess(abs((t - times[k]).total_seconds()), 1.0)
            # float view in [0,1]
            fl = v[2]
            self.assertEqual(fl.shape, (48, 64))
            self.assertLessEqual(float(fl.max()), 1.0)

    def test_mono16(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cap16.ser"
            base = _mk_frames(3)
            frames = [f.astype(np.uint16) * 256 for f in base]
            ser_io.write_ser(p, frames, pixel_depth_bits=16)
            v = ser_io.read_ser(p)
            self.assertEqual(v.meta.pixel_depth_bits, 16)
            raw = v.frame_raw(1)
            self.assertEqual(raw.dtype, np.uint16)
            np.testing.assert_array_equal(raw, frames[1])
            fl = v[1]
            self.assertAlmostEqual(float(fl[2, 2]), float(frames[1][2, 2]) / 65535.0, places=4)

    def test_rgb24(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "caprgb.ser"
            frames = _mk_frames(3, color=True)
            ser_io.write_ser(p, frames)
            v = ser_io.read_ser(p)
            self.assertEqual(v.meta.color, "rgb")
            self.assertEqual(v.meta.color_id, 100)
            np.testing.assert_array_equal(v.frame_raw(0), frames[0])

    def test_float_input_autoscale16(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.ser"
            frames = [np.linspace(0, 1, 48 * 64).reshape(48, 64) for _ in range(2)]
            ser_io.write_ser(p, frames)
            v = ser_io.read_ser(p)
            self.assertEqual(v.meta.pixel_depth_bits, 16)
            self.assertAlmostEqual(float(v[0].max()), 1.0, places=3)

    def test_empty_write_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                ser_io.write_ser(Path(d) / "x.ser", [])


class TestSERRobustness(unittest.TestCase):
    def test_bad_magic(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.ser"
            p.write_bytes(b"NOTASERFILE____" + b"\x00" * 400)
            with self.assertRaises(ValueError):
                ser_io.read_ser(p)
            with self.assertRaises(ValueError):
                ser_io.read_video(p)

    def test_truncated_pixels(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cap.ser"
            frames = _mk_frames(3)
            ser_io.write_ser(p, frames)
            data = p.read_bytes()[:-100]  # chop the tail
            p.write_bytes(data)
            with self.assertRaises(ValueError):
                ser_io.read_ser(p)


class TestAVIRoundTrip(unittest.TestCase):
    def test_mono8(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cap.avi"
            frames = _mk_frames(4)
            ser_io.write_avi(p, frames, fps=60.0)
            v = ser_io.read_video(p)
            self.assertEqual(v.meta.container, "avi")
            self.assertEqual(v.meta.n_frames, 4)
            self.assertEqual((v.meta.width, v.meta.height), (64, 48))
            self.assertAlmostEqual(v.meta.fps, 60.0, delta=1.0)
            for k, f in enumerate(frames):
                np.testing.assert_array_equal(v.frame_raw(k), f)

    def test_rgb24(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cap_c.avi"
            frames = _mk_frames(3, color=True)
            ser_io.write_avi(p, frames, fps=30.0)
            v = ser_io.read_avi(p)
            self.assertEqual(v.meta.color, "rgb")
            for k, f in enumerate(frames):
                np.testing.assert_array_equal(v.frame_raw(k), f)

    def test_float_input(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.avi"
            ramp = np.linspace(0, 1, 48 * 64).reshape(48, 64)
            ser_io.write_avi(p, [ramp, ramp])
            v = ser_io.read_avi(p)
            got = v[0]
            self.assertAlmostEqual(float(got.min()), 0.0, places=3)
            self.assertAlmostEqual(float(got.max()), 1.0, places=3)

    def test_compressed_fourcc_rejected(self):
        """A fake MJPG-compressed AVI must be rejected with a transcode hint,
        not silently mis-decoded."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mjpg.avi"
            frames = _mk_frames(1)
            ser_io.write_avi(p, frames)
            data = bytearray(p.read_bytes())
            # patch the strh fccHandler 'DIB ' -> 'MJPG'
            idx = data.find(b"DIB ")
            self.assertGreater(idx, 0)
            data[idx:idx + 4] = b"MJPG"
            # and biCompression in strf (=0 offset 16 bytes into BITMAPINFOHEADER)
            bi = data.find(struct.pack("<Iii", 40, 64, 48))
            self.assertGreater(bi, 0)
            struct.pack_into("<I", data, bi + 16, struct.unpack("<I", b"GPJM"[::-1])[0])
            p.write_bytes(bytes(data))
            with self.assertRaises(ValueError) as cm:
                ser_io.read_avi(p)
            self.assertIn("ranscode", str(cm.exception))


class TestVideoSequenceAPI(unittest.TestCase):
    def test_iteration_and_slicing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.ser"
            frames = _mk_frames(6)
            ser_io.write_ser(p, frames)
            v = ser_io.read_video(p)
            self.assertEqual(len(v), 6)
            idxs = [i for i, _ in v.iter_frames(step=2)]
            self.assertEqual(idxs, [0, 2, 4])
            sl = v[1:4]
            self.assertEqual(sl.shape, (3, 48, 64))
            self.assertEqual(int(v[0][2, 2] * 255), 0)
            self.assertEqual(int(v.frame_raw(3)[2, 2]), 30)
            with self.assertRaises(IndexError):
                v.frame_raw(6)

    def test_summary(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.avi"
            ser_io.write_avi(p, _mk_frames(10), fps=50.0)
            s = ser_io.video_summary(p)
            self.assertEqual(s["n_frames"], 10)
            self.assertAlmostEqual(s["fps"], 50.0, delta=1.0)
            self.assertAlmostEqual(s["seconds_est"], 0.2, delta=0.01)


if __name__ == "__main__":
    unittest.main()
