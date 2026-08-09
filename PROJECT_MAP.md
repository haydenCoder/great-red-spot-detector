# Project map

**User guide:** `docs/GRS_OBSERVATORY_BOOK.md`  
**Case study:** `docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md`  
**Long technical essay:** `docs/PROFESSOR_TECHNICAL_ESSAY.md`  
**Version:** see `VERSION` (**6.9.0**)

```
great-red-spot-detector/   (or GRS_Observatory/)
├── RUN_ME.command
├── VERSION
├── README.md
├── docs/
│   ├── GRS_OBSERVATORY_BOOK.md      ← main operator guide
│   ├── TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md
│   ├── PROFESSOR_TECHNICAL_ESSAY.md
│   ├── PROMOTE_COPY_PASTE.md
│   ├── SECURITY.md
│   └── reference/                   ← architecture / module notes
├── app/
│   ├── desktop_app.py               ← macOS desktop UI
│   ├── desktop_pipeline.py          ← Process orchestration
│   ├── server.py + templates/       ← optional local web UI
│   ├── champion_measure.py
│   ├── superduper.py                ← best-answer card (SUPERDUPER_*.txt)
│   ├── publish_primary.py
│   ├── spice_auto.py · ephemeris_pro.py
│   ├── planet_models.py             ← Planet profiles (v6.7): Jupiter/Saturn/Neptune/Uranus/Mars
│   ├── planetary_stacker.py         ← planet-generalised stacker (per-lat / flow / global warp)
│   ├── planetary_derotator.py       ← planet-generalised derotator (measurement/prior/hybrid)
│   ├── flow_warp.py                 ← dense 2D optical-flow warp (v6.7.1, SNR-weighted in v6.8)
│   ├── frame_quality.py             ← lucky-imaging frame rejection (v6.7.1)
│   ├── ser_io.py                    ← SER/AVI capture reader-writer (v6.8)
│   ├── ap_stacker.py                ← AutoStakkert-class APS stacker + drizzle + derotate_frames (v6.8)
│   ├── observatory_pipeline.py      ← video-stack / sharpen / animate / jupos / video-to-answer (v6.8)
│   ├── sharpen_lab.py               ← RegiStax-style wavelets, RL, unsharp (v6.8)
│   ├── transits.py                  ← GRS + Galilean moon transit planner (v6.8)
│   ├── grs_ellipse.py               ← rim-ellipse estimator, 5th measurement definition (v6.8)
│   ├── animation.py · jupos_io.py   ← blink GIF export · JUPOS CSV (v6.8)
│   ├── video_synth.py               ← rotating-video ground truth (v6.8)
│   ├── image_warp.py                ← exact sub-pixel shift (FFT-ramp replacement, v6.8.x)
│   ├── rgb_combine.py               ← derotation-exact RGB channel combine (v6.9)
│   ├── filter_wheel.py              ← 3× SER capture → derotated RGB one-shot (v6.9)
│   ├── wind_analysis.py             ← zonal-wind fit: m/s offset vs System-III (v6.9)
│   ├── grs_drift.py                 ← GRS CM-II drift fit + prediction cone (v6.9)
│   ├── session_planner.py           ← max capture span / filter-window planner (v6.9)
│   ├── stack_report.py              ← drizzle forensics + dither-diversity audit (v6.9)
│   ├── limb_darkening.py            ← μ^k limb-darkening coefficient fit (v6.9)
│   ├── models/                      ← SPIRE-Net weights (frozen)
│   └── outputs/                     ← job folders after Process
├── tools/
│   ├── zonal_stacker_benchmark.py   ← Jupiter-zonal stacker benchmark
│   ├── flow_warp_benchmark.py       ← reproducible warp-mode A/B (v6.7.1)
│   └── real_photo_stack.py          ← all-modes run on real frames + report (v6.7.5)
└── tests/
```

After **Process**, open the job folder and read **`SUPERDUPER_BEST_ANSWER.txt`** (one-page “report this” card).
