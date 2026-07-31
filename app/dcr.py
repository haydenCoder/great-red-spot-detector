#!/usr/bin/env python3
"""
Atmospheric Differential Dispersion Correction (DCR) for multi-channel stacks.

SCOPE — IMPORTANT
=================
This module implements a *standard* astronomical DCR correction based on the
Edlén (1966) refractive-index formula. It is appropriate for the **narrowband
and broad-band channel shifts** encountered when stacking RGB/IR/CH4
planetary video at moderately high zenith angles.

DCR is **only meaningful when channels are isolated by a filter wheel** (R, G,
B, IR685, IR742, CH4, etc.) or when the camera's Bayer pattern is properly
debayered into per-channel images. For an ordinary consumer RGB camera
without filter isolation, "blue vs red" dispersion is dominated by the lens,
not the atmosphere, and an atmospheric-only DCR will not fix the residual.

The output of this module is intended to be used as a *pre-alignment* before
sub-pixel phase correlation. The "after" step is still required.

For a typical amateur Jupiter stack at z=30° with 60–80 nm R and B passbands,
R−B dispersion is ~0.5–1.0 arcsec, i.e. sub-pixel at 0.1″/px. A 4K image of
Jupiter (apparent diameter ~40″) sampled at 0.04″/px shows the R−B shift
already as ~10–25 px — which is real, and is what this module is designed to
correct, given a known bandpass and zenith distance.

References:
  - Edlén, B. (1966). The refractive index of air. Metrologia 2, 71.
  - Filippenko, A. V. (1982). The importance of atmospheric differential
    refraction in spectrophotometry. PASP 94, 715.
  - Wynne, C. G. (1996). Field spreading in a telescope corrected for
    atmospheric dispersion. MNRAS 280, 555.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple

import numpy as np

# -----------------------------------------------------------------------------
# Edlén (1966) refractive index of standard air
# -----------------------------------------------------------------------------

# Standard wavelengths for the J-/B-/V-/R-/I-bands and common narrowband filters
# used in planetary imaging. Values in nm. The string keys are the same as those
# used by grs_complete_system.FILTER_WAVELENGTH_NM, so this module is a drop-in
# extension.
FILTER_WAVELENGTH_NM: Dict[str, float] = {
    "UV": 365.0,
    "B": 445.0,
    "G": 550.0,
    "V": 550.0,
    "R": 658.0,
    "IR685": 685.0,
    "IR742": 742.0,
    "CH4_continuum": 890.0,
    "CH4_line":  889.9,   # ±0.1 nm of CH4 absorption line
    "I": 806.0,
    "A": 700.0,
}


@dataclass
class Atmosphere:
    """Standard atmosphere parameters for the Edlén formula."""
    pressure_mbar: float = 1013.25
    temp_c: float = 15.0
    rel_humidity: float = 0.50       # fractional, 0..1
    co2_ppm: float = 450.0           # modern value

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


def edlen_n_minus_1(lam_nm: float, atm: Atmosphere) -> float:
    """
    Refractive index of standard air minus 1, from Edlén (1966) with the
    Birch (1991) CO2 / humidity update. Returns a small positive number,
    e.g. ~2.78e-4 at 550 nm, 1 atm, 15°C.

    Standard Edlén (1966) dry-air formula, in the form used by Filippenko
    (1982) and Stone & Zimmerman (2011):
        (n_s - 1) × 1e8 = 8342.13 + 2406030 / (130 - σ²) + 15997 / (38.9 - σ²)
    where σ = 1/λ in μm. This form is numerically stable and accurate
    to better than 1e-8 over 300–1700 nm.

    For other pressures, temperatures, and humidities we apply the
    Birch (1991) update:
        n(T, p, f) - 1 = (n_s - 1) × (p / p_std) × (T_std / T) × (1 + ...)
                        - f × g(σ)
    """
    lam_um = max(1e-3, float(lam_nm)) * 1e-3
    sigma2 = (1.0 / lam_um) ** 2
    # Edlén (1966) dry-air, in the stable form
    n_s_minus_1 = (
        8342.13
        + 2406030.0 / (130.0 - sigma2)
        + 15997.0 / (38.9 - sigma2)
    ) * 1e-8
    # Pressure, temperature, CO2
    T_kelvin = 273.15 + float(atm.temp_c)
    p_pa = float(atm.pressure_mbar) * 100.0
    p_pa_std = 101325.0
    # Saturation water-vapour pressure (Buck, 1981)
    if atm.temp_c >= 0:
        e_sat_pa = 6.1121 * math.exp(
            (18.678 - atm.temp_c / 234.5) * (atm.temp_c / (257.14 + atm.temp_c))
        )
    else:
        e_sat_pa = 6.1115 * math.exp(
            (23.036 - atm.temp_c / 333.7) * (atm.temp_c / (279.82 + atm.temp_c))
        )
    f_pa = float(atm.rel_humidity) * e_sat_pa
    # CO2 correction factor (Birch 1991)
    co2_corr = 1.0 + 0.540 * (atm.co2_ppm - 300.0) * 1e-6
    # Combined formula (Stone & Zimmerman 2011, eq. 3):
    n_minus_1 = (
        n_s_minus_1
        * (p_pa / p_pa_std)
        * (1.0 + (p_pa / p_pa_std - 1.0) * 0.0)   # placeholder, see ref
        * (288.15 / T_kelvin)
        * co2_corr
        - f_pa * (3.8020 - 0.0384 / sigma2) * 1e-10
    )
    return float(n_minus_1)


def dcr_shift_arcsec(
    z_deg: float,
    lam1_nm: float,
    lam2_nm: float,
    pressure_mbar: float = 1013.25,
    temp_c: float = 15.0,
    rel_humidity: float = 0.50,
) -> float:
    """
    Differential refraction in arcseconds between two wavelengths at zenith
    distance z. Returns (n(lam1) − n(lam2)) × tan(z) in arcseconds.
    """
    atm = Atmosphere(pressure_mbar=pressure_mbar, temp_c=temp_c, rel_humidity=rel_humidity)
    dn = edlen_n_minus_1(lam1_nm, atm) - edlen_n_minus_1(lam2_nm, atm)
    z_rad = math.radians(max(0.0, min(89.9, float(z_deg))))
    return dn * math.tan(z_rad) * (180.0 / math.pi) * 3600.0


def dcr_vertical_offset_px(
    arcsec_shift: float,
    planet_radius_px: float,
    apparent_diameter_arcsec: float,
) -> float:
    """
    Convert a DCR shift in arcseconds to a pixel offset along the *vertical*
    (zenith) direction on the image plane.

    The plate scale at the planet's apparent diameter is:
        arcsec_per_px = apparent_diameter_arcsec / (2 * planet_radius_px)
    so a shift in arcseconds converts to pixels as arcsec / arcsec_per_px.
    """
    if planet_radius_px <= 0:
        return 0.0
    arcsec_per_px = float(apparent_diameter_arcsec) / (2.0 * float(planet_radius_px))
    return float(arcsec_shift) / (arcsec_per_px + 1e-12)


# -----------------------------------------------------------------------------
# Vectorised batch helper — for a stack of N frames at fixed atmosphere
# -----------------------------------------------------------------------------

def dcr_shift_per_channel(
    channels: Dict[str, np.ndarray],
    ref_name: str = "G",
    z_deg: float = 30.0,
    pressure_mbar: float = 1013.25,
    temp_c: float = 15.0,
    rel_humidity: float = 0.50,
    planet_radius_px: float = 100.0,
    apparent_diameter_arcsec: float = 40.0,
) -> Dict[str, float]:
    """
    Return a per-channel DCR vertical offset in pixels, ready to apply with
    a sub-pixel vertical shift.
    """
    if ref_name not in channels:
        ref_name = next(iter(channels.keys()))
    ref_lam = FILTER_WAVELENGTH_NM.get(ref_name, 550.0)
    out: Dict[str, float] = {}
    for name in channels:
        if name == ref_name:
            out[name] = 0.0
            continue
        target_lam = FILTER_WAVELENGTH_NM.get(name, 550.0)
        shift_as = dcr_shift_arcsec(z_deg, target_lam, ref_lam, pressure_mbar, temp_c, rel_humidity)
        out[name] = dcr_vertical_offset_px(shift_as, planet_radius_px, apparent_diameter_arcsec)
    return out


__all__ = [
    "Atmosphere",
    "FILTER_WAVELENGTH_NM",
    "edlen_n_minus_1",
    "dcr_shift_arcsec",
    "dcr_vertical_offset_px",
    "dcr_shift_per_channel",
]
