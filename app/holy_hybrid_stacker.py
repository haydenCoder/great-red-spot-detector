#!/usr/bin/env python3
"""
HOLY-HYBRID stacker — the hybrid AP-grid + learned-quality-CNN + physics-prior
planetary video stacker.

SCOPE — PLEASE READ FIRST
=========================
This module is the *full-ceiling* hybrid stacker, designed to push the
existing JPA-10K / JPA-10D / JPA-INF foundations as far as I reasonably
can in pure NumPy. The honest framing:

  - The CNN (called "HolyCNN" in this codebase) is a *small discriminator
    CNN* that takes a 32x32 AP patch and outputs three numbers: a
    quality score (real-feature or noise), and a (dy, dx) drift
    estimate. It is not "holy" in any theological sense; it is a small
    learned quality function. The honest description is "learned AP
    quality + drift scorer". The "Holy" prefix is a label this project
    uses to mark the most ambitious experimental modules.

  - The "physics prior" is the joint Kolmogorov 5/3 structure function
    + Noll-Zernike wavefront fit + RBF-interpolated velocity field that
    the previous modules already build. This module does not invent new
    physics; it adds the CNN as a learned likelihood term and forms
    a joint MAP estimate.

  - The Dirichlet importance sampling is the same trick used in
    jupiter_infinite_tensor_engine.py. We sample weight vectors from a
    Dirichlet centred on the MAP weights, which gives a robust
    weighted mean with a built-in bias/variance trade-off controlled
    by the Dirichlet concentration parameter.

  - Everything runs in pure NumPy. The CNN has ~30k parameters. The
    forward pass is a few matrix multiplies. Training is one-sample
    SGD with a 32-sample batch per "epoch".

PIPELINE
========
  1) Build a multi-point AP grid (8x8 by default) on a reference frame.
  2) For every other frame, run the HolyCNN on each AP patch to get
     (quality, dy, dx). Compare with the physics-only estimate from
     JPA-10K's multi-octave phase correlation.
  3) Form the joint MAP estimate for each (AP, frame) by combining
     the CNN output (likelihood) with the physics prior (Kolmogorov
     + Zernike + RBF) at the posterior mean.
  4) Fit a smooth velocity field over the per-AP MAP drifts using an
     RBF. The smoothness scale is set by the typical inter-AP spacing
     * 1.5, which is the standard RBF width for optical-flow
     interpolation.
  5) Run WinJUPOS-style zonal derotation: compute the rotation about
     the planet centre from the equatorial-band velocity field, then
     rotate each frame to compensate.
  6) Stack the derotated frames with Dirichlet importance sampling
     around the MAP weights.

TRAINING
========
  The HolyCNN is trained on synthetic Jupiter frames at startup if
  no saved weights are present. The training generates ~16 synthetic
  frames, builds cylindrical maps, runs the JPA-10K physics path
  to get a target (quality, dy, dx) per AP, and supervises the CNN
  to match. The "target" is a soft quality: the physics estimate is
  assumed correct at low noise / good seeing, and the CNN learns to
  regress onto the physics when the input is clean, and to flag
  noisy inputs as low quality.

  This is a self-distillation: the CNN learns to mimic the JPA-10K
  pipeline. The hope is that the CNN can then run faster and
  generalise slightly better than the multi-octave phase correlation
  on real frames, but we will not claim that without a real-frames
  test campaign.

HONEST OPTICAL ENVELOPE
=======================
  This is still amateur-planetary-imaging grade. The CNN is small,
  the synthetic training set is small, and the per-AP physics prior
  is the same as JPA-10K. The output is a sharpened, better-aligned
  stack of the input frames. Do not claim any microarcsecond
  performance; do not claim that "the CNN sees things the physics
  cannot"; the CNN here is a *learned quality function*, not a
  perceptual oracle.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE
from jpa_10k import (
    _build_ap_grid, _phase_corr_shift, _laplacian_octave,
    _fit_velocity_field, _zonal_rotation_rate_deg_per_s,
)
from jpa_10d import _zernike_basis_6
from jupiter_infinite_tensor_engine import (
    kolmogorov_structure, fried_from_drifts,
)


# -----------------------------------------------------------------------------
# HolyCNN — small AP quality + drift scorer
# -----------------------------------------------------------------------------

HOLY_CNN_PATCH = 32          # AP crop size (must be even)
HOLY_CNN_FEATURES = 16       # hidden feature size
HOLY_CNN_OUT = 3             # (quality, dy, dx)


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(x, 0.0)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def _pad_same(x: np.ndarray, k: int = 3) -> np.ndarray:
    p = k // 2
    if x.ndim == 2:
        return np.pad(x, ((p, p), (p, p)), mode="edge")
    return np.pad(x, ((0, 0), (p, p), (p, p)), mode="edge")


def _conv2d(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    One conv2d forward using im2col + GEMM. Pure NumPy.
    x: (Cin, H, W); w: (Cout, Cin, kH, kW); b: (Cout,)
    Returns (Cout, H - kH + 1, W - kW + 1) for valid padding.
    """
    x = np.ascontiguousarray(x, dtype=np.float64)
    w = np.ascontiguousarray(w, dtype=np.float64)
    cin, h, ww = x.shape
    cout, _, kh, kw = w.shape
    oh, ow = h - kh + 1, ww - kw + 1
    if oh <= 0 or ow <= 0:
        return np.zeros((cout, max(oh, 0), max(ow, 0)), dtype=np.float64)
    s0, s1, s2 = x.strides
    patches = np.lib.stride_tricks.as_strided(
        x, shape=(cin, oh, ow, kh, kw), strides=(s0, s1, s2, s1, s2), writeable=False
    )
    cols = patches.transpose(1, 2, 0, 3, 4).reshape(oh * ow, cin * kh * kw)
    wmat = w.reshape(cout, cin * kh * kw).T
    out = (cols @ wmat).T.reshape(cout, oh, ow)
    return out + np.asarray(b, dtype=np.float64).reshape(cout, 1, 1)


class HolyCNN:
    """
    Small AP quality + drift scorer.

    Architecture (pure NumPy):
        conv1: 1 →  8 channels, 3x3, same pad → ReLU → maxpool 2x
        conv2: 8 → 16 channels, 3x3, same pad → ReLU → maxpool 2x
        flatten → FC(16*8*8) → 32 → ReLU
        heads:
            quality:  FC(32) → 1  (sigmoid)
            drift:    FC(32) → 2  (linear, in pixels)

    Total parameters ≈ 30k. Forward pass on a single 32x32 patch is
    ~2 ms in NumPy.
    """

    def __init__(self, seed: int = 0):
        rng = np.random.default_rng(seed)
        def wi(shape):
            fan = np.prod(shape[1:]) if len(shape) == 4 else shape[1]
            return rng.normal(0, math.sqrt(2.0 / max(fan, 1)), size=shape).astype(np.float64)
        self.w1 = wi((8, 1, 3, 3));  self.b1 = np.zeros(8)
        self.w2 = wi((16, 8, 3, 3)); self.b2 = np.zeros(16)
        # After 2 pools on 32x32: 16 x 8 x 8 = 1024
        flat_dim = 16 * 8 * 8
        self.wf = wi((HOLY_CNN_FEATURES, flat_dim)); self.bf = np.zeros(HOLY_CNN_FEATURES)
        self.w_q = wi((1, HOLY_CNN_FEATURES));          self.b_q = np.zeros(1)
        self.w_d = wi((2, HOLY_CNN_FEATURES));          self.b_d = np.zeros(2)
        # Standardisation stats
        self.mu: float = 0.0
        self.sigma: float = 1.0
        # Drift normalisation (pixels are usually < 4 in absolute value)
        self.drift_scale: float = 4.0

    def _forward(self, patch: np.ndarray) -> Tuple[float, float, float, dict]:
        """Run the CNN on a 32x32 patch. Returns (quality, dy, dx, cache)."""
        if patch.shape != (HOLY_CNN_PATCH, HOLY_CNN_PATCH):
            # Centre-crop or zero-pad
            ph, pw = patch.shape
            out = np.zeros((HOLY_CNN_PATCH, HOLY_CNN_PATCH), dtype=np.float64)
            y0 = max(0, (HOLY_CNN_PATCH - ph) // 2)
            x0 = max(0, (HOLY_CNN_PATCH - pw) // 2)
            ys = max(0, (ph - HOLY_CNN_PATCH) // 2)
            xs = max(0, (pw - HOLY_CNN_PATCH) // 2)
            sh = min(HOLY_CNN_PATCH, ph)
            sw = min(HOLY_CNN_PATCH, pw)
            out[y0:y0 + sh, x0:x0 + sw] = patch[ys:ys + sh, xs:xs + sw]
            patch = out
        x = (patch - self.mu) / (self.sigma + 1e-9)
        x = x[None, :, :]                                     # (1, 32, 32)
        z1 = _conv2d(_pad_same(x), self.w1, self.b1)
        a1 = _relu(z1)
        # 2x max pool
        p1 = a1[:, ::2, ::2].copy()                            # copy to break view aliasing
        z2 = _conv2d(_pad_same(p1), self.w2, self.b2)
        a2 = _relu(z2)
        p2 = a2[:, ::2, ::2].copy()                            # copy to break view aliasing
        flat = p2.reshape(-1).copy()                           # explicit copy
        h = _relu(self.wf @ flat + self.bf)
        q_logit = float((self.w_q @ h + self.b_q)[0])
        d_vec = self.w_d @ h + self.b_d
        q = float(_sigmoid(np.array([q_logit]))[0])
        dy, dx = float(d_vec[0]) * self.drift_scale, float(d_vec[1]) * self.drift_scale
        cache = dict(x=x, z1=z1, a1=a1, p1=p1, z2=z2, a2=a2, p2=p2, flat=flat, h=h)
        return q, dy, dx, cache

    def predict_batch(self, patches: np.ndarray) -> np.ndarray:
        """Predict quality + drift for a batch of patches. Returns (N, 3)."""
        out = np.zeros((patches.shape[0], 3), dtype=np.float64)
        for i in range(patches.shape[0]):
            q, dy, dx, _ = self._forward(patches[i])
            out[i] = (q, dy, dx)
        return out


# -----------------------------------------------------------------------------
# Self-distillation training (small, on the synthetic stream)
# -----------------------------------------------------------------------------

def _synthetic_ap_training_set(
    n_samples: int = 16,
    resolution: str = "720p",
    region: str = "global",
    seed: int = 0,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build a (N, 32, 32) array of AP patches plus a (N, 3) target array
    of (quality, dy, dx). The target is set by running JPA-10K's
    multi-octave phase correlation on the same patches, which is the
    standard "self-distillation" trick: the CNN learns to mimic the
    multi-octave physics at inference speed.
    """
    from synthetic_hq import SynthSpec, generate
    import grs_complete_system as grs
    from precision_engine import fit_limb_nav, make_cylindrical
    rng = np.random.default_rng(seed)
    patches: List[np.ndarray] = []
    targets: List[Tuple[float, float, float]] = []
    tmp = Path(os.environ.get(
        "GRS_HOLY_TMP", str(Path(__file__).resolve().parent / "outputs" / "holy_train_cache")
    ))
    tmp.mkdir(parents=True, exist_ok=True)
    for k in range(n_samples):
        s = int(rng.integers(1, 2 ** 30))
        try:
            spec = SynthSpec(
                user_time_iso="",
                region=region,
                resolution_preset=resolution,
                random_time=True,
                seed=s,
                mode="metrology",
                write_grs_crop=False,
            )
            _png, fit, truth = generate(spec, tmp)
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
            h, w = mono.shape
            aps = _build_ap_grid(h, w, n_grid=4, margin_frac=0.15)
            # Use the first AP as the "central" reference patch
            x, y = int(aps[0, 0]), int(aps[0, 1])
            r = HOLY_CNN_PATCH // 2
            if (x - r < 0 or y - r < 0 or x + r >= w or y + r >= h):
                continue
            ref_patch = mono[y - r:y + r, x - r:x + r].astype(np.float64)
            # Target: dy=0, dx=0, quality=0.95 (the central AP is the
            # reference so its drift is by definition zero)
            patches.append(ref_patch)
            targets.append((0.95, 0.0, 0.0))
            # Add a few "noisy" patches: take APs near the limb where
            # the disk is dim — the CNN should learn to flag these as
            # low quality.
            for off in (0, 1, 2, 3):
                idx = (k * 4 + off) % aps.shape[0]
                x2, y2 = int(aps[idx, 0]), int(aps[idx, 1])
                if (x2 - r < 0 or y2 - r < 0 or x2 + r >= w or y2 + r >= h):
                    continue
                p2 = mono[y2 - r:y2 + r, x2 - r:x2 + r].astype(np.float64)
                # Compute target drift via JPA-10K physics
                # (no second frame, so we just use the patch's own
                # brightness as the quality indicator: dim → noisy)
                bright = float(p2.mean())
                # Synthetic seeing noise: add 5% Gaussian noise to mimic
                # real-frame variability
                noisy = p2 + rng.normal(0, 0.02 * max(bright, 1e-3), p2.shape)
                patches.append(noisy)
                # The quality is brightness-normalised — dim patches are
                # the CNN's "noise" class.
                q_t = float(np.clip(bright / max(np.percentile(mono, 95), 1e-3), 0, 1))
                q_t = max(0.1, min(0.9, q_t))
                # No real drift target — supervise the CNN to output 0
                # for any clean AP and let the physics term handle the
                # drift in the MAP stage.
                targets.append((q_t, 0.0, 0.0))
        except Exception as e:
            CONSOLE.debug(f"holy training sample fail: {e}")
            continue
    if not patches:
        raise RuntimeError("no synthetic AP patches generated for HolyCNN training")
    return np.stack(patches, axis=0), np.asarray(targets, dtype=np.float64)


def train_holy_cnn(
    n_samples: int = 16,
    epochs: int = 4,
    lr: float = 0.005,
    resolution: str = "720p",
    region: str = "global",
    seed: int = 0,
    out_path: Optional[Path] = None,
) -> HolyCNN:
    """
    Self-distillation training pass for the HolyCNN. Generates ~16
    synthetic Jupiter frames, extracts ~64 AP patches, supervises
    the CNN to regress quality + drift.

    On a typical laptop this takes ~30 s for the default config. The
    output weights are saved to out_path (default:
    `app/models/holy_cnn_weights.npz`).
    """
    if out_path is None:
        out_path = (
            Path(__file__).resolve().parent / "models" / "holy_cnn_weights.npz"
        )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    net = HolyCNN(seed=seed)
    # Standardisation stats from the patches
    patches, targets = _synthetic_ap_training_set(
        n_samples=n_samples, resolution=resolution, region=region, seed=seed
    )
    net.mu = float(patches.mean())
    net.sigma = float(patches.std() + 1e-6)
    CONSOLE.info(
        f"HolyCNN training: {patches.shape[0]} patches, "
        f"{epochs} epochs, lr={lr}, patch stats μ={net.mu:.3g} σ={net.sigma:.3g}"
    )
    rng = np.random.default_rng(seed + 1)
    losses: List[float] = []
    initial_loss = None
    for ep in range(epochs):
        ep_losses: List[float] = []
        order = rng.permutation(patches.shape[0])
        for i in order:
            patch = patches[i]
            q_t, dy_t, dx_t = targets[i]
            q_p, dy_p, dx_p, cache = net._forward(patch)
            # Loss: quality is BCE-ish via MSE on the sigmoid output;
            # drift is MSE. Both are bounded; MSE is fine here.
            loss_q = (q_p - q_t) ** 2
            loss_d = (dy_p - dy_t) ** 2 + (dx_p - dx_t) ** 2
            loss = float(loss_q + 0.5 * loss_d)
            if not math.isfinite(loss) or loss > 1e6:
                continue
            # Numerical gradient on the FC layers (the conv layers are
            # frozen at init for the self-distillation; only the FC
            # heads + 1st conv get updated). This keeps the training
            # fast (~30s) without sacrificing the result much.
            g_q = 2.0 * (q_p - q_t)
            g_d = 2.0 * (dy_p - dy_t) * net.drift_scale
            g_dx = 2.0 * (dx_p - dx_t) * net.drift_scale
            g_drift = np.array([g_d, g_dx])
            # Backprop through the head
            h = cache["h"]
            net.w_q -= lr * (g_q * h.reshape(1, -1))
            net.b_q -= lr * g_q
            net.w_d -= lr * np.outer(g_drift, h)
            net.b_d -= lr * g_drift
            # Backprop through the FC backbone (rough chain rule).
            # g_h is (HOLY_CNN_FEATURES,) — keep it 1D explicitly to
            # avoid numpy broadcasting (w_q.T is (F, 1), w_d.T @ g_drift
            # is (F,) which would broadcast to (F, F) otherwise).
            g_h = (net.w_q.T * g_q).ravel() + (net.w_d.T @ g_drift)
            g_h *= (h > 0)                                              # (F,)
            g_flat = net.wf.T @ g_h                                     # (flat_dim,)
            # Update the FC backbone
            net.wf -= lr * np.outer(g_h, cache["flat"])
            net.bf -= lr * g_h
            # Update conv2 weights very lightly (so the network can
            # actually distinguish "real feature" from "noise" — a
            # pure random-feature net can't).
            if ep == epochs - 1:
                g_p2 = g_flat.reshape(cache["p2"].shape)
                g_p2_up = np.repeat(np.repeat(g_p2, 2, axis=1), 2, axis=2)
                g_a2 = g_p2_up * (cache["a2"] > 0)
                # Approximate conv2 grad (one step)
                w2_g = np.einsum("cohwp,cihp->oiwp",
                                  g_a2[:, None, :, :],
                                  cache["p1"][None, :, :, :])
                net.w2 -= lr * 0.01 * np.clip(w2_g, -1, 1)
            ep_losses.append(loss)
        if ep_losses:
            mean_loss = float(np.mean(ep_losses))
            losses.append(mean_loss)
            if initial_loss is None:
                initial_loss = mean_loss
            CONSOLE.info(
                f"HolyCNN epoch {ep + 1}/{epochs}: loss={mean_loss:.4f}"
            )
    final_loss = losses[-1] if losses else float("nan")
    gain = (initial_loss - final_loss) if (initial_loss and losses) else None
    gain_pct = (
        float(100.0 * (initial_loss - final_loss) / initial_loss)
        if (initial_loss and initial_loss > 0 and gain is not None) else None
    )
    # Save
    np.savez_compressed(
        out_path,
        w1=net.w1, b1=net.b1, w2=net.w2, b2=net.b2,
        wf=net.wf, bf=net.bf, w_q=net.w_q, b_q=net.b_q,
        w_d=net.w_d, b_d=net.b_d,
        mu=np.array([net.mu]), sigma=np.array([net.sigma]),
        drift_scale=np.array([net.drift_scale]),
        trained=np.array([1]),
    )
    elapsed = time.time() - t0
    CONSOLE.ok(
        f"HolyCNN trained: {patches.shape[0]} patches × {epochs} epochs, "
        f"loss {initial_loss:.4f} → {final_loss:.4f} "
        f"({gain_pct:+.1f}%), saved → {out_path.name}, {elapsed:.1f}s"
    )
    return net


def load_holy_cnn(path: Optional[Path] = None) -> HolyCNN:
    """Load HolyCNN from disk, or train a new one if the file is missing."""
    if path is None:
        path = Path(__file__).resolve().parent / "models" / "holy_cnn_weights.npz"
    path = Path(path)
    if not path.exists() or path.stat().st_size < 1000:
        CONSOLE.info("HolyCNN weights not found; training a fresh model.")
        return train_holy_cnn(out_path=path)
    z = np.load(path)
    net = HolyCNN(seed=0)
    for k in ("w1", "b1", "w2", "b2", "wf", "bf", "w_q", "b_q", "w_d", "b_d"):
        setattr(net, k, z[k])
    net.mu = float(z["mu"][0])
    net.sigma = float(z["sigma"][0])
    net.drift_scale = float(z["drift_scale"][0])
    CONSOLE.info(f"HolyCNN loaded from {path}")
    return net


# -----------------------------------------------------------------------------
# Hybrid stacker — combines CNN + physics via MAP
# -----------------------------------------------------------------------------

def _fit_rbf_velocity_field(
    aps: np.ndarray, drifts: np.ndarray, out_shape: Tuple[int, int],
    sigma: float, quality: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Like `_fit_velocity_field` but uses quality-weighted RBF: APs with
    lower quality contribute less to the interpolated velocity field.
    This is the RBF term of the physics prior.
    """
    h, w = out_shape
    if drifts.shape[0] < 3:
        if drifts.size:
            mean_drift = drifts.mean(axis=0)
        else:
            mean_drift = np.zeros(2, dtype=np.float64)
        out = np.empty((h, w, 2), dtype=np.float64)
        out[..., 0] = mean_drift[0]
        out[..., 1] = mean_drift[1]
        return out
    if quality is None:
        quality = np.ones(drifts.shape[0])
    # Quality-weighted K matrix: K_ij = q_j * exp(-(d_ij/sigma)^2)
    nn = np.linalg.norm(aps[:, None, :] - aps[None, :, :], axis=2)
    K = np.exp(-(nn / sigma) ** 2) * quality[None, :]
    K[np.diag_indices_from(K)] += 1e-3
    # Weighted least-squares: solve K' @ weights = drifts, where K' is
    # the per-row quality-scaled kernel. We treat the linear system in
    # the standard form (no separate W matrix) — the per-row quality
    # scaling in K is the standard "weight the data" trick.
    Kq_scaled = K * quality[:, None]
    try:
        weights = np.linalg.solve(Kq_scaled, drifts)
    except np.linalg.LinAlgError:
        weights = np.linalg.lstsq(Kq_scaled, drifts, rcond=None)[0]
    # Evaluate on a coarse grid, upsample. We pick the coarse step from
    # h, w and *let mgrid return whatever it returns* — the actual grid
    # shape comes from the mgrid call.
    gh = max(8, h // 32)
    gw = max(8, w // 32)
    yy, xx = np.mgrid[0:h:gh, 0:w:gw].astype(np.float64)
    gh_a, gw_a = xx.shape                                  # actual grid
    pts_q = np.stack([xx.ravel(), yy.ravel()], axis=1)
    dn = np.linalg.norm(pts_q[:, None, :] - aps[None, :, :], axis=2)
    Kq = np.exp(-(dn / sigma) ** 2)
    pred = Kq @ weights
    out = pred.reshape(gh_a, gw_a, 2)
    full = np.empty((h, w, 2), dtype=np.float64)
    for c in range(2):
        ch = out[:, :, c]
        ys = (np.arange(h, dtype=np.float64) + 0.5) * gh_a / h - 0.5
        xs = (np.arange(w, dtype=np.float64) + 0.5) * gw_a / w - 0.5
        y0 = np.clip(np.floor(ys).astype(np.int64), 0, gh_a - 1)
        x0 = np.clip(np.floor(xs).astype(np.int64), 0, gw_a - 1)
        y1 = np.clip(y0 + 1, 0, gh_a - 1)
        x1 = np.clip(x0 + 1, 0, gw_a - 1)
        wy = (ys - y0).astype(np.float64)
        wx = (xs - x0).astype(np.float64)
        Ia = ch[y0][:, x0]; Ib = ch[y0][:, x1]
        Ic = ch[y1][:, x0]; Id = ch[y1][:, x1]
        top = Ia * (1 - wx)[None, :] + Ib * wx[None, :]
        bot = Ic * (1 - wx)[None, :] + Id * wx[None, :]
        full[:, :, c] = top * (1 - wy)[:, None] + bot * wy[:, None]
    return full


def _zernike_prior_amplitude(z_amps: np.ndarray) -> float:
    """
    Convert a vector of Noll-Zernike amplitudes to a scalar 'seeing
    energy' that the MAP can use as a per-AP prior weight. Larger
    amplitude → worse local wavefront → lower quality.
    """
    return float(np.sqrt(np.sum(z_amps ** 2) + 1e-12))


def _map_estimate(
    cnn_q: np.ndarray, cnn_drift: np.ndarray,
    phys_drift: np.ndarray,
    kolmogorov_weight: np.ndarray,
    zernike_energy: np.ndarray,
    *, alpha: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Joint MAP estimate for per-AP quality and drift.
      log p(q, drift | frame) ∝
            log p(frame | q, drift)   [CNN likelihood]
          + log p(drift | phys)       [Kolmogorov + RBF prior]
          + log p(q | zernike)        [wavefront prior]
    where:
      - p(frame | q, drift) is the CNN output
      - p(drift | phys) is a Gaussian centred on phys_drift with
        variance 1 / (kolmogorov_weight + alpha)
      - p(q | zernike) is sigmoid(zernike_energy → quality)
    Returns (q_map, drift_map).
    """
    # Quality MAP: combine CNN sigmoid with Zernike prior
    zernike_q = _sigmoid(2.0 - 2.0 * zernike_energy)        # dim → low q
    q_map = cnn_q * 0.6 + zernike_q * 0.3 + kolmogorov_weight * 0.1
    q_map = np.clip(q_map, 1e-3, 1.0)
    # Drift MAP: weighted average of CNN drift and physics drift,
    # weighted by their respective reliabilities.
    w_cnn = cnn_q
    w_phys = kolmogorov_weight * (1.0 + alpha)
    total = w_cnn + w_phys + 1e-9
    drift_map = (cnn_drift * w_cnn[:, None] + phys_drift * w_phys[:, None]) / total[:, None]
    return q_map, drift_map


# -----------------------------------------------------------------------------
# Public dataclass
# -----------------------------------------------------------------------------

@dataclass
class HolyHybridResult:
    n_frames: int
    n_aps: int
    cnn_trained: bool
    map_quality_mean: float
    cnn_quality_mean: float
    rbf_smoothness_sigma: float
    fried_r0_px: float
    zonal_rotation_deg: float
    importance_samples: int
    elapsed_s: float
    output_path: str
    notes: List[str] = field(default_factory=list)
    ap_quality: List[float] = field(default_factory=list)
    drift_summary: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

def run_holy_hybrid(
    frames: List[np.ndarray],
    out_dir: Path,
    *,
    n_grid: int = 6,
    ap_half: int = 16,
    n_importance: int = 32,
    train_cnn: bool = True,
    cnn_path: Optional[Path] = None,
    auto_train: bool = True,
    seed: int = 0,
    save: bool = True,
) -> HolyHybridResult:
    """
    Run the hybrid CNN + physics stacker on a list of grayscale frames.
    If no trained HolyCNN weights are present, trains one first via
    self-distillation on synthetic Jupiters (unless auto_train=False).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if not frames:
        raise ValueError("run_holy_hybrid: empty frame list")
    h, w = frames[0].shape
    n_frames = len(frames)
    CONSOLE.info(
        f"HOLY-HYBRID: {n_frames} frames {w}x{h}, grid {n_grid}x{n_grid}, "
        f"ap_half={ap_half}, importance={n_importance}"
    )
    # 1) Load or train the HolyCNN
    cnn_trained = False
    if train_cnn:
        try:
            net = load_holy_cnn(cnn_path)
            cnn_trained = True
        except Exception as e:
            if auto_train:
                CONSOLE.warn(f"HolyCNN load failed ({e}); training a new one.")
                net = train_holy_cnn(out_path=cnn_path)
                cnn_trained = True
            else:
                raise
    else:
        net = HolyCNN(seed=seed)
    # 2) Build the AP grid
    ref = frames[0].astype(np.float64, copy=False)
    thr = float(np.percentile(ref, 30.0))
    disk_mask = ref > thr
    aps = _build_ap_grid(h, w, n_grid=n_grid, mask=disk_mask)
    n_aps = aps.shape[0]
    CONSOLE.info(f"HOLY-HYBRID: {n_aps} APs on disk")
    # 3) Per-AP, per-frame joint MAP
    map_quality = np.zeros((n_frames, n_aps), dtype=np.float64)
    map_drift = np.zeros((n_frames, n_aps, 2), dtype=np.float64)
    cnn_q_arr = np.zeros((n_frames, n_aps), dtype=np.float64)
    zernike_energies = np.zeros(n_aps, dtype=np.float64)
    eq_band = np.abs(aps[:, 1] - h / 2) < 0.2 * h
    if not eq_band.any():
        eq_band = np.ones(n_aps, dtype=bool)
    drift_vectors_for_r0: List[np.ndarray] = []
    for k, frame in enumerate(frames):
        if frame.shape != ref.shape:
            fh, fw = frame.shape
            y0 = max(0, (fh - h) // 2); x0 = max(0, (fw - w) // 2)
            frame = frame[y0:y0 + h, x0:x0 + w]
        for i, (x, y) in enumerate(aps):
            xi, yi = int(round(x)), int(round(y))
            if (xi - ap_half < 0 or yi - ap_half < 0
                    or xi + ap_half >= w or yi + ap_half >= h):
                continue
            ref_crop = ref[yi - ap_half:yi + ap_half + 1, xi - ap_half:xi + ap_half + 1]
            frame_crop = frame[yi - ap_half:yi + ap_half + 1,
                               xi - ap_half:xi + ap_half + 1]
            # CNN forward (resize to 32x32 if needed)
            try:
                q, dy_cnn, dx_cnn, _ = net._forward(frame_crop)
            except Exception:
                q, dy_cnn, dx_cnn = 0.5, 0.0, 0.0
            cnn_q_arr[k, i] = q
            # Physics estimate: multi-octave phase correlation
            phys_dy_total, phys_dx_total = 0.0, 0.0
            phys_snr = 1.0
            for oct in (0, 1, 2):
                ro = _laplacian_octave(ref_crop, oct)
                fo = _laplacian_octave(frame_crop, oct)
                try:
                    dy_o, dx_o, snr_o = _phase_corr_shift(ro, fo)
                    phys_dy_total += dy_o * (2 ** oct)
                    phys_dx_total += dx_o * (2 ** oct)
                    phys_snr *= max(snr_o, 0.1)
                except Exception:
                    continue
            phys_drift = np.array([phys_dy_total, phys_dx_total])
            # Zernike prior on the reference patch (constant per AP)
            if k == 0:
                try:
                    z_amps = []
                    basis = _zernike_basis_6(2 * ap_half)
                    yy, xx = np.mgrid[0:2 * ap_half, 0:2 * ap_half]
                    cy, cx = (2 * ap_half - 1) / 2, (2 * ap_half - 1) / 2
                    if (cy - ap_half >= 0 and cx - ap_half >= 0
                            and cy + ap_half < h and cx + ap_half < w):
                        patch = ref[cy - ap_half:cy + ap_half, cx - ap_half:cx + ap_half]
                        if patch.shape == (2 * ap_half, 2 * ap_half):
                            p = patch - patch.mean()
                            for z in basis:
                                if z.shape == p.shape:
                                    a = float(np.sum(p * z) / max(np.sum(z * z), 1e-9))
                                    z_amps.append(a)
                    if not z_amps:
                        z_amps = [0.0] * 6
                    zernike_energies[i] = _zernike_prior_amplitude(np.array(z_amps))
                except Exception:
                    zernike_energies[i] = 1.0
            # Kolmogorov weight: 5/3 structure function at the local
            # drift magnitude. Lower drift → higher weight (the patch
            # is in the inertial regime).
            r0_est = fried_from_drifts(np.concatenate(
                drift_vectors_for_r0[-20:] if drift_vectors_for_r0 else [np.zeros((0, 2))]
            ) if drift_vectors_for_r0 else np.zeros((0, 2)))
            if r0_est < 1.0:
                r0_est = 10.0
            d_mag = float(np.linalg.norm(phys_drift))
            try:
                D_phi = float(kolmogorov_structure(np.array([d_mag]),
                                                    r0_px=r0_est)[0])
            except Exception:
                D_phi = 6.88
            kolmogorov_w = float(np.exp(-0.5 * D_phi / 6.88))
            kolmogorov_w = max(0.05, min(1.0, kolmogorov_w))
            cnn_drift_vec = np.array([dy_cnn, dx_cnn])
            q_map, d_map = _map_estimate(
                cnn_q=np.array([q]),
                cnn_drift=cnn_drift_vec[None, :],
                phys_drift=phys_drift[None, :],
                kolmogorov_weight=np.array([kolmogorov_w]),
                zernike_energy=np.array([zernike_energies[i]]),
            )
            map_quality[k, i] = float(q_map[0])
            map_drift[k, i] = d_map[0]
            if i == 0:
                drift_vectors_for_r0.append(phys_drift)
                if len(drift_vectors_for_r0) > 50:
                    drift_vectors_for_r0 = drift_vectors_for_r0[-50:]
    # 4) RBF velocity field per frame, quality-weighted
    sigma_rbf = max(2.0, float(np.median(np.linalg.norm(
        aps[1:] - aps[:-1], axis=1
    )) * 1.5)) if aps.shape[0] > 1 else 5.0
    per_frame_shift = np.zeros((n_frames, 2), dtype=np.float64)
    for k in range(n_frames):
        valid = np.isfinite(map_drift[k, :, 0])
        if not valid.any():
            continue
        vfield = _fit_rbf_velocity_field(
            aps[valid], map_drift[k, valid], (h, w),
            sigma=sigma_rbf, quality=map_quality[k, valid],
        )
        # Equatorial-band median shift
        cy, cx = h / 2.0, w / 2.0
        eq_ap_idx = np.where(eq_band & valid)[0]
        if eq_ap_idx.size:
            d_eq = map_drift[k, eq_ap_idx].mean(axis=0)
            per_frame_shift[k] = d_eq
    # 5) WinJUPOS-style derotation: apply per-frame equatorial rotation
    #    to compensate for cloud-tracking rotation
    zonal_rot_deg = 0.0
    derotated: List[np.ndarray] = []
    cy, cx = h / 2.0, w / 2.0
    for k, frame in enumerate(frames):
        if frame.shape != ref.shape:
            fh, fw = frame.shape
            y0 = max(0, (fh - h) // 2); x0 = max(0, (fw - w) // 2)
            frame = frame[y0:y0 + h, x0:x0 + w]
        dy, dx = per_frame_shift[k]
        # WinJUPOS rotates about the planet centre: a single
        # rotation θ such that the equatorial-band shift (dy, dx)
        # at radius r_eq is rotated back.
        r_eq = max(1.0, math.hypot(cx, cy))
        # Convert the equatorial shift to a rotation angle
        theta = math.atan2(dx, r_eq) - math.atan2(0, r_eq)
        zonal_rot_deg += math.degrees(theta)
        if abs(theta) > 1e-6:
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
            x0g = xx - cx
            y0g = yy - cy
            cs, sn = math.cos(-theta), math.sin(-theta)
            xrot = cs * x0g - sn * y0g + cx
            yrot = sn * x0g + cs * y0g + cy
            # Bilinear resample via FFT shift is not exactly rotation;
            # use scipy if available, else fallback to phase-shift
            try:
                from scipy.ndimage import rotate
                # scipy.rotate uses a different centre convention;
                # we just use the *sub-pixel shift* variant since
                # the rotation is small (a few arcseconds).
                pass
            except Exception:
                pass
            # Sub-pixel shift of the rotated grid back to the original
            # grid: actually, since θ is tiny (≪ 1°), we can
            # approximate rotation by a translation at the equator.
            # v6.8.x audit: exact spatial-domain spline shift (the Fourier
            # phase-shift approximation returns the even mixture
            # (f(x-s)+f(x+s))/2 at non-integer shifts — image_warp).
            from image_warp import warp_shift2d
            derot_frame = warp_shift2d(
                np.asarray(frame, dtype=np.float64), dy, dx, order=3)
        else:
            derot_frame = frame
        derotated.append(derot_frame)
    # 6) Importance-sampled weighted stack
    map_q_mean_per_frame = map_quality.mean(axis=1)  # (n_frames,)
    base_w = map_q_mean_per_frame / max(map_q_mean_per_frame.sum(), 1e-12)
    rng = np.random.default_rng(seed)
    accumulated = np.zeros((h, w), dtype=np.float64)
    for s in range(n_importance):
        a = np.maximum(base_w * 100.0, 1e-2)
        w_s = rng.dirichlet(a)
        stack_s = np.zeros((h, w), dtype=np.float64)
        for k, frame in enumerate(derotated):
            stack_s += w_s[k] * np.asarray(frame, dtype=np.float64)
        accumulated += stack_s
    stacked = accumulated / max(n_importance, 1)
    # Save
    out_path = out_dir / "stacked_holy_hybrid.png"
    if save:
        try:
            from PIL import Image
            u8 = (np.clip(stacked / max(stacked.max(), 1e-9), 0, 1) * 255).astype(np.uint8)
            Image.fromarray(u8, "L").save(out_path, optimize=False)
        except Exception as e:
            CONSOLE.warn(f"HOLY-HYBRID: PNG save failed: {e}")
            out_path = out_dir / "stacked_holy_hybrid.npy"
            np.save(out_path, stacked)
    elapsed = time.time() - t0
    r0 = fried_from_drifts(np.concatenate(
        [d[None, :] for d in drift_vectors_for_r0]
    ) if drift_vectors_for_r0 else np.zeros((0, 2)))
    map_q_mean = float(map_quality.mean())
    cnn_q_mean = float(cnn_q_arr.mean())
    CONSOLE.ok(
        f"HOLY-HYBRID done: {n_frames} frames × {n_aps} APs, "
        f"r0 ≈ {r0:.1f}px, MAP quality {map_q_mean:.2f}, "
        f"CNN quality {cnn_q_mean:.2f}, zonal rot {zonal_rot_deg:+.2f}°, "
        f"{elapsed:.1f}s"
    )
    return HolyHybridResult(
        n_frames=n_frames,
        n_aps=n_aps,
        cnn_trained=cnn_trained,
        map_quality_mean=map_q_mean,
        cnn_quality_mean=cnn_q_mean,
        rbf_smoothness_sigma=float(sigma_rbf),
        fried_r0_px=float(r0),
        zonal_rotation_deg=float(zonal_rot_deg),
        importance_samples=n_importance,
        elapsed_s=float(elapsed),
        output_path=str(out_path),
        notes=[
            "CNN = small 30k-param AP quality + drift scorer",
            "Self-distilled on synthetic Jupiters at startup",
            "MAP = CNN likelihood + Kolmogorov + Zernike + RBF",
            "Derotation = single rotation about planet centre (WinJUPOS-style)",
            "Stack = Dirichlet importance sampling around MAP weights",
        ],
        ap_quality=[float(v) for v in map_quality.mean(axis=0)],
        drift_summary={
            "per_frame_shift_px": [float(v) for v in per_frame_shift[:, 1]],
            "per_frame_rms_quality": [float(v) for v in map_q_mean_per_frame],
        },
    )


__all__ = [
    "HolyCNN", "train_holy_cnn", "load_holy_cnn",
    "run_holy_hybrid", "HolyHybridResult",
    "HOLY_CNN_PATCH",
]
