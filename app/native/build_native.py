#!/usr/bin/env python3
"""Build the optional native geometry core (grscore).

    python app/native/build_native.py

Produces grscore.<abi>.so next to this file. Entirely optional: the engine
falls back to NumPy when the module is absent, so a failed build never breaks
the product.
"""
import subprocess, sys, sysconfig
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent

def main() -> int:
    inc = [sysconfig.get_paths()["include"], np.get_include()]
    ext = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    out = HERE / f"grscore{ext}"
    cmd = ["cc", "-O3", "-fPIC", "-shared", "-ffast-math", "-funroll-loops",
           *[f"-I{p}" for p in inc], str(HERE / "grscore.c"), "-o", str(out), "-lm"]
    print(" ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode == 0:
        print(f"built {out}")
    else:
        print("native build failed — engine will use the NumPy fallback")
    return r.returncode

if __name__ == "__main__":
    raise SystemExit(main())
