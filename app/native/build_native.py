#!/usr/bin/env python3
"""
Build the optional native geometry core (grscore).

    python3 app/native/build_native.py
    python3 app/native/build_native.py --openmp   # parallelise over CPU cores

Produces grscore.<abi>.so next to this file. Entirely optional: the engine
falls back to NumPy when the module is absent, so a failed build never breaks
the product.

The C kernel implements the three hot paths of the GRS metrology engine:
  - project_grid: spheroid lon/lat -> pixel coords (inner loop of
    make_cylindrical)
  - bilinear_map: bilinear resample at those coords (second inner loop
    of make_cylindrical)
  - limb_rays: isophote ray-trace used by fit_limb_nav (the 720-ray
    loop that dominates limb fit cost)
  - phase_corr_batch: per-AP crop extraction for the JPA stacker,
    OpenMP-parallel when --openmp is on

With --openmp, the kernel uses all available cores on the
multi-octave phase-correlation loop. Without it, the kernel is still
multi-threaded internally via Py_BEGIN_ALLOW_THREADS for the parts
that release the GIL.

Honest build choices:
  - We use CC = "cc" (the system C compiler). On Linux this is gcc
    or clang. On macOS this is the Xcode CLT cc.
  - We do NOT link FFTW: numpy's FFT is already a C call to MKL or
    FFTPack, and re-implementing it in C99 would be slower.
  - We do NOT auto-detect SIMD vectorisation beyond -O3 -ffast-math
    -funroll-loops. -march=native is *opt-in* via --march-native
    because it locks the binary to one CPU family.
"""
import argparse
import subprocess
import sys
import sysconfig
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the grscore native extension.")
    ap.add_argument("--openmp", action="store_true",
                    help="enable OpenMP (parallel over CPU cores)")
    ap.add_argument("--march-native", action="store_true",
                    help="enable -march=native (locks binary to one CPU family)")
    args = ap.parse_args()

    inc = [sysconfig.get_paths()["include"], np.get_include()]
    ext = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    out = HERE / f"grscore{ext}"

    cmd = [
        "cc", "-O3", "-fPIC", "-shared",
        "-ffast-math", "-funroll-loops",
    ]
    if args.march_native:
        cmd.append("-march=native")
    if args.openmp:
        cmd.append("-fopenmp")
    cmd += [f"-I{p}" for p in inc]
    cmd += [str(HERE / "grscore.c"), "-o", str(out), "-lm"]
    if args.openmp:
        # On Linux, link against the OpenMP runtime so the .so can
        # spawn threads when loaded by Python. -lgomp is the GCC
        # runtime; clang uses the same flag on most distros.
        cmd += ["-lgomp"]
    print(" ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode == 0:
        print(f"built {out}")
        return 0
    print("native build failed — engine will use the NumPy fallback")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
