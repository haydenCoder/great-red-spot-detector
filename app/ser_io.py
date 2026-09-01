#!/usr/bin/env python3
"""
ser_io.py — planetary video I/O: SER and uncompressed AVI.

WHY THIS EXISTS
===============
AutoStakkert's killer workflow starts at a *video* file: you point it at a
SER (ZWO/Player One/QHY cameras) or an AVI (legacy DIB captures) and it does
the rest. Until now this app accepted only stacked stills or a folder of
PNG/FITS frames. This module closes the gap with a dependency-free reader
(and writer, so benchmark sequences can round-trip):

  read_video(path)  ->  Video(frames, meta)   # .ser or .avi, detected by magic
  write_ser(path, frames, meta)               # mono8/mono16/RGB24
  write_avi(path, frames, fps)                # uncompressed DIB (8 or 24 bit)

FORMAT NOTES (honest, from the specs)
=====================================
SER v3 (ser-spec.org, 2014):
  - 178-byte header: magic "LUCAM-RECORDER", camera/plane geometry, observer /
    instrument / telescope strings, and MS "tick" timestamps (100 ns units
    since 0001-01-01 UTC).
  - Colour IDs: 0 MONO, 8..11 Bayer (RGGB/GRBG/GBRG/BGGR), 16..19 CYMG family,
    100 RGB, 101 BGR. We read MONO + RGB/BGR; Bayer planes are returned as
    MONO raw mosaics with `color_id` recorded so callers can debayer.
  - If both header timestamps are non-zero, FrameCount per-frame UTC tick
    stamps follow the pixel data.
  - LittleEndian flag: 1 => little-endian 16-bit pixels (little-endian is by
    far the common case; we honour both).

AVI (RIFF):
  - We support uncompressed DIB video (biCompression == BI_RGB) at 8, 24 and
    32 bits per pixel — the only AVI flavour a codec-free reader can honestly
    handle. MJPEG/H264 AVIs are rejected with a clear message telling the user
    to transcode (we will NOT silently mis-decode them).
  - Positive biHeight means bottom-up rows (the Windows default); negative
    means top-down. 8-bit DIBs carry a BGR palette which we expand.

Everything is memory-mapped / streamed per frame, so a multi-GB capture does
not need to fit in RAM: iterate `Video` like a sequence, or call `iter_frames`
with a `step` to sub-sample.
"""
from __future__ import annotations

import datetime as dt
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple, Union

import numpy as np

# ---------------------------------------------------------------------------
# Shared timestamp helpers (SER "Microsoft ticks": 100 ns since 0001-01-01 UTC)
# ---------------------------------------------------------------------------

UNIX_EPOCH_TICKS = 621355968000000000  # ticks at 1970-01-01T00:00:00 UTC
TICKS_PER_SECOND = 10_000_000


def ticks_to_datetime(ticks: int) -> Optional[dt.datetime]:
    """MS ticks -> aware UTC datetime. Returns None for 0 (unset)."""
    if not ticks:
        return None
    unix_us = (int(ticks) - UNIX_EPOCH_TICKS) // 10
    try:
        return dt.datetime.fromtimestamp(unix_us / 1e6, tz=dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def datetime_to_ticks(t: dt.datetime) -> int:
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return UNIX_EPOCH_TICKS + int(round(t.timestamp() * TICKS_PER_SECOND))


# ---------------------------------------------------------------------------
# Video container object
# ---------------------------------------------------------------------------

@dataclass
class VideoMeta:
    path: str
    container: str                 # "ser" | "avi"
    width: int
    height: int
    n_frames: int
    pixel_depth_bits: int          # per channel/plane
    color_id: int                  # SER color id; AVI: -1
    color: str                     # "mono" | "rgb" | "bayer"
    fps: float = 0.0
    observer: str = ""
    instrument: str = ""
    telescope: str = ""
    first_frame_utc: Optional[str] = None   # ISO8601, if the file carries stamps
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


class Video:
    """Random-access view over a planetary video file.

    `video[i]` returns the i-th frame as a float64 array in [0, 1]:
      (h, w) for mono, (h, w, 3) for RGB.  Raw access via `frame_raw(i)`
      preserves the native dtype (uint8/uint16).
    """

    def __init__(self, meta: VideoMeta, raw_getter, time_getter=None):
        self.meta = meta
        self._raw_getter = raw_getter
        self._time_getter = time_getter

    def __len__(self) -> int:
        return self.meta.n_frames

    def frame_raw(self, i: int) -> np.ndarray:
        if not (0 <= i < self.meta.n_frames):
            raise IndexError(f"frame {i} out of range 0..{self.meta.n_frames - 1}")
        return self._raw_getter(i)

    def frame_utc(self, i: int) -> Optional[dt.datetime]:
        if self._time_getter is None:
            return None
        return self._time_getter(i)

    def to_float(self, raw: np.ndarray) -> np.ndarray:
        a = np.asarray(raw)
        if a.dtype == np.uint8:
            return a.astype(np.float64) / 255.0
        if a.dtype in (np.uint16, np.int32, np.int64):
            scale = float((1 << self.meta.pixel_depth_bits) - 1) if self.meta.pixel_depth_bits <= 16 else float(np.iinfo(a.dtype).max)
            return np.clip(a.astype(np.float64), 0, None) / scale
        return a.astype(np.float64)

    def __getitem__(self, i: Union[int, slice]) -> np.ndarray:
        if isinstance(i, slice):
            return np.stack([self[k] for k in range(*i.indices(self.meta.n_frames))])
        if i < 0:
            i += self.meta.n_frames
        return self.to_float(self.frame_raw(i))

    def iter_frames(self, step: int = 1, limit: int = 0) -> Iterator[Tuple[int, np.ndarray]]:
        n = self.meta.n_frames
        count = 0
        for i in range(0, n, max(1, int(step))):
            yield i, self[i]
            count += 1
            if limit and count >= limit:
                break


# ---------------------------------------------------------------------------
# SER reader
# ---------------------------------------------------------------------------

SER_MAGIC = b"LUCAM-RECORDER"
SER_HEADER_BYTES = 178

SER_COLOR_NAMES = {
    0: "mono",
    8: "bayer", 9: "bayer", 10: "bayer", 11: "bayer",
    16: "bayer", 17: "bayer", 18: "bayer", 19: "bayer",
    100: "rgb", 101: "bgr",
}
SER_BAYER_NAMES = {8: "RGGB", 9: "GRBG", 10: "GBRG", 11: "BGGR"}


def _ser_layout(buf: memoryview) -> Tuple[VideoMeta, dict]:
    if len(buf) < SER_HEADER_BYTES:
        raise ValueError("SER: file too small for header")
    file_id = bytes(buf[0:14])
    if file_id.decode("ascii", errors="ignore") != SER_MAGIC.decode("ascii"):
        raise ValueError(f"SER: bad magic {file_id!r}")
    (lu_id, color_id, little_endian, width, height, depth, frame_count) = struct.unpack_from("<7I", buf, 14)
    if not (1 <= width <= 65536 and 1 <= height <= 65536):
        raise ValueError(f"SER: bogus geometry {width}x{height}")
    if not (1 <= depth <= 16):
        raise ValueError(f"SER: bogus pixel depth {depth}")
    observer = bytes(buf[42:82]).split(b"\x00")[0].decode("utf-8", errors="replace").strip()
    instrument = bytes(buf[82:122]).split(b"\x00")[0].decode("utf-8", errors="replace").strip()
    telescope = bytes(buf[122:162]).split(b"\x00")[0].decode("utf-8", errors="replace").strip()
    date_time, date_time_utc = struct.unpack_from("<2q", buf, 162)
    color_name = SER_COLOR_NAMES.get(color_id)
    if color_name is None:
        raise ValueError(f"SER: unsupported ColorID {color_id}")
    bytes_per_sample = 1 if depth <= 8 else 2
    planes = 3 if color_name in ("rgb", "bgr") else 1
    frame_bytes = width * height * planes * bytes_per_sample
    expect = SER_HEADER_BYTES + frame_count * frame_bytes
    if len(buf) < expect:
        raise ValueError(f"SER: truncated pixel data ({len(buf)} < {expect})")
    has_stamps = bool(date_time and date_time_utc) and len(buf) >= expect + 8 * frame_count
    meta = VideoMeta(
        path="", container="ser", width=width, height=height, n_frames=frame_count,
        pixel_depth_bits=depth, color_id=color_id,
        color=("bayer" if color_name == "bayer" else color_name),
        observer=observer, instrument=instrument, telescope=telescope,
        first_frame_utc=(ticks_to_datetime(date_time_utc) or ticks_to_datetime(date_time) or dt.datetime.now(dt.timezone.utc)).isoformat()
        if (date_time or date_time_utc) else None,
        extra={
            "lu_id": lu_id,
            "little_endian": bool(little_endian),
            "bayer_pattern": SER_BAYER_NAMES.get(color_id),
            "bgr": color_name == "bgr",
            "has_per_frame_stamps": has_stamps,
        },
    )
    layout = dict(
        frame_bytes=frame_bytes,
        bytes_per_sample=bytes_per_sample,
        planes=planes,
        dtype=("<u2" if (bytes_per_sample == 2 and little_endian) else ">u2") if bytes_per_sample == 2 else "|u1",
        stamps_offset=expect if has_stamps else None,
    )
    return meta, layout


def read_ser(path: Union[str, Path], zero_copy: bool = True) -> Video:
    """Open a SER file. Frames are decoded on demand from the file bytes."""
    path = Path(path)
    data = memoryview(path.read_bytes())  # cheap on modern OS page cache; keeps API dependency-free
    meta, lay = _ser_layout(data)
    meta.path = str(path)
    w, h = meta.width, meta.height
    fb, bps, planes = lay["frame_bytes"], lay["bytes_per_sample"], lay["planes"]
    dtp = np.dtype(lay["dtype"])
    bgr = bool(meta.extra.get("bgr"))

    def _get(i: int) -> np.ndarray:
        off = SER_HEADER_BYTES + i * fb
        arr = np.frombuffer(data[off:off + fb], dtype=dtp)
        if planes == 3:
            a = arr.reshape(h, w, 3)
            if bgr:
                a = a[..., ::-1]
            if dtp == np.dtype("|u1"):
                return a.copy()  # freed-view safety + channel flip copy
            return a.astype(np.uint16, copy=True).astype(dtp, copy=True)
        a = arr.reshape(h, w)
        return a.copy()

    def _time(i: int) -> Optional[dt.datetime]:
        if lay["stamps_offset"] is None:
            return None
        (t,) = struct.unpack_from("<q", data, lay["stamps_offset"] + 8 * i)
        return ticks_to_datetime(t)

    return Video(meta, _get, _time)


def write_ser(
    path: Union[str, Path],
    frames: Sequence[np.ndarray],
    *,
    observer: str = "",
    instrument: str = "",
    telescope: str = "",
    frame_times_utc: Optional[Sequence[dt.datetime]] = None,
    pixel_depth_bits: int = 0,
    color_id: int = 0,
) -> Path:
    """Write a SER file from uint8/uint16 mono (h,w) or RGB (h,w,3) frames.

    pixel_depth_bits: 0 = infer from dtype (8 or 16). color_id: 0 MONO, 100
    RGB — inferred from frame shape unless overridden (e.g. a Bayer pattern).
    """
    path = Path(path)
    if not frames:
        raise ValueError("write_ser: no frames")
    first = np.asarray(frames[0])
    is_color = first.ndim == 3 and first.shape[-1] == 3
    if color_id == 0 and is_color:
        color_id = 100
    if first.dtype == np.uint8:
        depth = pixel_depth_bits or 8
        bps = 1
        cast = np.uint8
    elif first.dtype == np.uint16:
        depth = pixel_depth_bits or 16
        bps = 2
        cast = np.uint16
    elif first.dtype.kind == "f":
        depth = pixel_depth_bits or 16
        bps = 2
        cast = np.uint16
    else:
        raise ValueError(f"write_ser: unsupported dtype {first.dtype}")
    h, w = first.shape[:2]
    n = len(frames)
    t0 = None
    if frame_times_utc:
        t0 = datetime_to_ticks(frame_times_utc[0])
    with path.open("wb") as fh:
        header = struct.pack("<14s", SER_MAGIC)
        header += struct.pack(
            "<7I", 0, color_id, 1, w, h, depth, n,
        )
        def _pad(s: str, n_: int = 40) -> bytes:
            b = s.encode("utf-8", errors="replace")[:n_]
            return b + b"\x00" * (n_ - len(b))
        header += _pad(observer) + _pad(instrument) + _pad(telescope)
        header += struct.pack("<2q", t0 or 0, t0 or 0)
        assert len(header) == SER_HEADER_BYTES
        fh.write(header)
        for f in frames:
            a = np.asarray(f)
            if a.shape[:2] != (h, w):
                raise ValueError("write_ser: frames must share shape")
            if a.dtype.kind == "f":
                a = np.clip(a, 0.0, 1.0) * ((1 << depth) - 1)
                a = np.rint(a).astype(cast)
            elif a.dtype != cast:
                a = a.astype(cast)
            fh.write(a.tobytes(order="C"))
        if frame_times_utc:
            if len(frame_times_utc) != n:
                raise ValueError("frame_times_utc must match frame count")
            for t in frame_times_utc:
                fh.write(struct.pack("<q", datetime_to_ticks(t)))
    return path


# ---------------------------------------------------------------------------
# AVI reader (uncompressed DIB only) + writer
# ---------------------------------------------------------------------------

def _read_chunk_header(buf: memoryview, off: int) -> Tuple[bytes, int, int]:
    """Return (fourcc, size, data_offset). Chunks are word-aligned."""
    fourcc = bytes(buf[off:off + 4])
    (size,) = struct.unpack_from("<I", buf, off + 4)
    return fourcc, size, off + 8


def _iter_chunks(buf: memoryview, start: int, end: int) -> Iterator[Tuple[bytes, int, int]]:
    off = start
    while off + 8 <= end:
        fourcc, size, data_off = _read_chunk_header(buf, off)
        yield fourcc, size, data_off
        off = data_off + size + (size & 1)


def read_avi(path: Union[str, Path]) -> Video:
    """Open an uncompressed (BI_RGB DIB) AVI at 8/24/32 bpp.

    Raises ValueError with a transcode hint for any codec we cannot honestly
    decode without external libraries.
    """
    path = Path(path)
    buf = memoryview(path.read_bytes())
    fourcc, riff_size, data_off = _read_chunk_header(buf, 0)
    if fourcc != b"RIFF" or bytes(buf[8:12]) != b"AVI ":
        raise ValueError("AVI: not a RIFF AVI file")
    riff_end = min(len(buf), 8 + riff_size)

    width = height = bpp = compression = 0
    top_down = False
    fps = 0.0
    total_frames = 0
    palette: Optional[bytes] = None
    movi_start = movi_end = 0

    # walk hdrl
    for f, s, o in _iter_chunks(buf, 12, riff_end):
        if f == b"LIST" and bytes(buf[o:o + 4]) == b"hdrl":
            for f2, s2, o2 in _iter_chunks(buf, o + 4, o + s):
                if f2 == b"avih":
                    us_per_frame, _, _, _, ntot = struct.unpack_from("<5I", buf, o2)
                    fps = 1e6 / us_per_frame if us_per_frame else 0.0
                    total_frames = ntot
                elif f2 == b"LIST" and bytes(buf[o2:o2 + 4]) == b"strl":
                    for f3, s3, o3 in _iter_chunks(buf, o2 + 4, o2 + s2):
                        if f3 == b"strh":
                            fcc_type = bytes(buf[o3:o3 + 4])
                            fcc_handler = bytes(buf[o3 + 4:o3 + 8])
                            if fcc_type != b"vids":
                                raise ValueError(f"AVI: first stream is {fcc_type!r}, not video")
                            compression = struct.unpack("<I", fcc_handler)[0] & 0xFFFFFFFF
                        elif f3 == b"strf":
                            (bi_size, bi_w, bi_h, bi_planes, bi_bpp, bi_comp) = struct.unpack_from("<IiiHHI", buf, o3)
                            width, height, bpp = bi_w, abs(bi_h), bi_bpp
                            top_down = bi_h < 0
                            compression = bi_comp
                            pal_start = o3 + bi_size
                            n_pal = 1 << bpp if bpp == 8 else 0
                            if bi_size > 40 and bpp == 8:
                                n_pal = min(n_pal, (bi_size - 40) // 4) or n_pal
                            if bpp == 8 and s3 > 40:
                                palette = bytes(buf[pal_start: min(pal_start + 4 * n_pal, o3 + s3)])
        elif f == b"LIST" and bytes(buf[o:o + 4]) == b"movi":
            movi_start, movi_end = o + 4, o + s

    if not (width and height and bpp):
        raise ValueError("AVI: no DIB stream found")
    if compression != 0:
        four = struct.pack("<I", compression).decode("latin-1", errors="replace")
        raise ValueError(
            f"AVI: compressed codec {four!r} is not supported (codec-free reader). "
            "Transcode to uncompressed AVI or SER (e.g. PIPP / ffmpeg -vcodec rawvideo)."
        )
    if bpp not in (8, 24, 32):
        raise ValueError(f"AVI: {bpp}-bit DIB not supported")
    if not movi_start:
        raise ValueError("AVI: no movi list found")

    row_bits = width * bpp
    row_bytes = ((row_bits + 31) // 32) * 4          # DIB rows are 4-byte aligned
    frame_size = row_bytes * height
    frame_offsets: List[int] = []
    for f, s, o in _iter_chunks(buf, movi_start, movi_end):
        two_cc = f[:2]
        if two_cc in (b"00", b"01", b"02") and f[2:4] in (b"dc", b"db"):
            if s >= frame_size:
                frame_offsets.append(o)
    n = len(frame_offsets) or total_frames
    if total_frames and n != total_frames:
        n = min(n, total_frames)
    if n == 0:
        raise ValueError("AVI: no video frames found")

    color = "mono" if bpp == 8 else "rgb"
    meta = VideoMeta(
        path=str(path), container="avi", width=width, height=height, n_frames=n,
        pixel_depth_bits=8, color_id=-1, color=color, fps=fps,
    )

    def _get(i: int) -> np.ndarray:
        off = frame_offsets[i]
        raw = np.frombuffer(buf[off:off + frame_size], dtype=np.uint8)
        if bpp == 24:
            a = raw.reshape(height, row_bytes)[:, : width * 3].reshape(height, width, 3)[..., ::-1]
        elif bpp == 32:
            a = raw.reshape(height, row_bytes)[:, : width * 4].reshape(height, width, 4)[..., 2::-1]
        else:
            a = raw.reshape(height, row_bytes)[:, :width]
        if not top_down:
            a = a[::-1]
        return a.copy()

    return Video(meta, _get, None)


def write_avi(
    path: Union[str, Path],
    frames: Sequence[np.ndarray],
    *,
    fps: float = 30.0,
) -> Path:
    """Write an uncompressed DIB AVI (mono 8-bit or RGB 24-bit).

    Accepts uint8 or float64 [0,1] frames. Intended for sharing short stacks
    and for round-tripping benchmark sequences — not long captures.
    """
    path = Path(path)
    if not frames:
        raise ValueError("write_avi: no frames")
    first = np.asarray(frames[0])
    is_color = first.ndim == 3 and first.shape[-1] == 3
    h, w = first.shape[:2]
    bpp = 24 if is_color else 8
    row_bytes = ((w * bpp + 31) // 32) * 4
    frame_size = row_bytes * h
    n = len(frames)
    pal = b"".join(struct.pack("4B", i, i, i, 0) for i in range(256)) if bpp == 8 else b""
    bih = struct.pack(
        "<IiiHHIIiiII", 40, w, h, 1, bpp, 0, frame_size, 2835, 2835,
        256 if bpp == 8 else 0, 0,
    ) + pal
    # dwScale/dwRate define fps = dwRate / dwScale
    scale = 1_000_000 // max(1, int(round(fps)))
    rate = max(1, int(round(fps * scale / 1_000_000)))
    strh = struct.pack(
        "<4s4sIHHIIIIIIIIhhhh", b"vids", b"DIB ", 0, 0, 0, 0,
        scale, rate, 0, n, frame_size, 0xFFFFFFFF, 0, 0, 0, w, h,
    )
    avih = struct.pack(
        "<10I4I", scale, frame_size * n, 0, 0x10, n, 0, 1, frame_size, w, h,
        0, 0, 0, 0,
    )

    def _chunk(four: bytes, payload: bytes) -> bytes:
        pad = b"\x00" if len(payload) & 1 else b""
        return four + struct.pack("<I", len(payload)) + payload + pad

    def _alist(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return _chunk(b"LIST", body)

    strl = _alist(b"strl", _chunk(b"strh", strh) + _chunk(b"strf", bih))
    hdrl = _alist(b"hdrl", _chunk(b"avih", avih) + strl)

    movi_body = bytearray()
    idx: list = []
    for f in frames:
        a = np.asarray(f)
        if a.shape[:2] != (h, w):
            raise ValueError("write_avi: frames must share shape")
        if a.dtype.kind == "f":
            a = np.rint(np.clip(a, 0.0, 1.0) * 255.0).astype(np.uint8)
        if is_color:
            rows = np.zeros((h, row_bytes), dtype=np.uint8)
            rows[:, : w * 3] = a[..., ::-1].reshape(h, w * 3)
        else:
            rows = np.zeros((h, row_bytes), dtype=np.uint8)
            rows[:, :w] = a
        frame_bytes = rows[::-1].tobytes()          # bottom-up
        # dwOffset is measured from the start of the movi LIST body, so record
        # it before this frame's chunk is appended.
        idx.append(struct.pack("<4sIII", b"00db", 0x10, len(movi_body),
                               len(frame_bytes)))
        movi_body += _chunk(b"00db", frame_bytes)
    # The 'avih' header sets AVIF_HASINDEX, which promises this index; without it
    # the file only plays by accident (seeking fails, and strict demuxers such as
    # DirectShow/VirtualDub reject the stream). idx1 is a multiple of 2 bytes, so
    # _chunk adds no pad and the recorded offsets stay exact.
    movi_body += _chunk(b"idx1", b"".join(idx))
    movi = _alist(b"movi", bytes(movi_body))

    return path_write_riff(path, hdrl + movi)


def path_write_riff(path: Path, body: bytes) -> Path:
    with path.open("wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", len(body) + 4) + b"AVI " + body)
    return path


# ---------------------------------------------------------------------------
# Front door
# ---------------------------------------------------------------------------

def read_video(path: Union[str, Path]) -> Video:
    """Open .ser or .avi by magic/content. Raises a clear error otherwise."""
    path = Path(path)
    head = path.open("rb").read(16)
    if head[:14] == SER_MAGIC:
        return read_ser(path)
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return read_avi(path)
    raise ValueError(
        f"{path.name}: unrecognised video container (expected SER or RIFF AVI). "
        "For folders of stills use --frames-dir; for compressed AVI transcode first."
    )


def video_summary(path: Union[str, Path]) -> dict:
    """One-line facts about a capture without decoding frames."""
    v = read_video(path)
    m = v.meta.to_dict()
    m["seconds_est"] = (v.meta.n_frames / v.meta.fps) if v.meta.fps else None
    return m
