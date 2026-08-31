# How to Run the Jupiter Great Red Spot Detector & Observatory Suite

This guide provides complete, step-by-step instructions for installing, configuring, running, testing, and troubleshooting the **Jupiter Great Red Spot Detector** across all available operational interfaces: the Web Observatory UI, the Desktop GUI, and the Unified Command-Line Interface (`cli.py`).

---

## 1. System Requirements & Environment Setup

### 1.1 Prerequisites
- **Python**: Version 3.10 or newer (tested on Python 3.10, 3.11, and 3.12).
- **Operating System**: Linux (Ubuntu 20.04+, Debian 11+, Fedora, Arch), macOS (12.0 Monterey or later, Intel & Apple Silicon), or Windows 10/11 via WSL2.
- **C Compiler (Optional but Recommended)**: `gcc` or `clang` for compiling the high-performance C99 spline acceleration kernels (`_cspeed.so` / `_cspeed.dylib`). If no C compiler is found, the system gracefully falls back to pure SciPy routines.

### 1.2 Virtual Environment & Dependency Installation

In your terminal, navigate to the repository root and execute:

```bash
# Clone or enter the repository directory
cd /path/to/great-red-spot-detector

# Create an isolated virtual environment
python3 -m venv .venv

# Activate the virtual environment
# On Linux / macOS:
source .venv/bin/activate
# On Windows (PowerShell):
# .venv\Scripts\Activate.ps1

# Upgrade pip and install package dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install pytest pytest-xdist
```

### 1.3 Core Dependencies
The system relies on the following key libraries:
- `numpy` (>=1.22): Fast array computing, vector mathematics, image matrices.
- `scipy` (>=1.9): Spline filters, multidimensional coordinates, root finding, optimization.
- `Pillow` (>=9.0): Image file reading, writing, and format transformations.
- `astropy` (>=5.0): Astronomical time conversions (UTC/TT/TDB) and coordinate frames.
- `flask` (>=2.3): Web UI REST backend and interactive local dashboard.
- `certifi` (>=2023.0.0): Secure SSL certificates for JPL Horizons network queries.
- `spiceypy` (>=6.0): NASA NAIF SPICE kernel interface for planetary ephemerides.
- `pyephem` (>=4.1, optional): Galilean moon transit calculations and fallback astronomical tables.

### 1.4 Compiling the High-Performance C Speed Kernel

To enable the 3.5× faster C spline interpolation and fused Lucas-Kanade step for alignment-point stacking and frame derotation:

```bash
python3 tools/build_cspeed.py
```

This compiles `app/cspeed.c` into `app/_cspeed.so` (Linux) or `app/_cspeed.dylib` (macOS). Verify it works with:

```bash
python3 -c "import sys; sys.path.insert(0, 'app'); import cspeed; print('CSpeed Status:', cspeed.status_note())"
```

---

## 2. Running the Web Observatory Interface

The Web Observatory provides an interactive, browser-based user interface with real-time ephemeris clocks, planetary disk navigation, interactive multi-method consensus breakdown, the Deterioration Lab, and video stacker panels.

### 2.1 Starting the Web Server

```bash
# From the repository root:
python3 app/server.py

# Or via the macOS launcher script:
./Launch_GRS_Observatory.command
```

By default, the server binds to `0.0.0.0:8765`. Open your web browser and navigate to:
```
http://localhost:8765
```
*(In cloud sandboxes or containers, access the port via the forwarded preview URL).*

### 2.2 Web Interface Features
1. **Interactive Image Analysis**: Upload single planetary images (FITS, PNG, JPG, TIFF). The UI parses FITS headers for `DATE-OBS` timestamps automatically or prompts for mid-exposure UTC.
2. **Ephemeris Resolution**: Real-time Jovian System I, II, and III Central Meridian longitudes, sub-Earth latitude ($D_E$), sub-solar latitude, phase angle, and North Polar Position Angle (PA).
3. **Disk & Limb Nav Tool**: Visual inspection of the fitted planetary limb ellipse, planetographic latitude grid lines, and Great Red Spot bounding boxes.
4. **Method Consensus Matrix**: Live comparison between Template Matching, Dark Centroid, Active Contour Ellipse, Redness Index, and Convolutional Neural Network (SPIRE-Net) estimators.
5. **Deterioration Lab**: Sweep resolution, atmospheric seeing blur, and sensor noise to evaluate the breakdown threshold of ground-based metrology.
6. **Sharpen Lab**: Interactive wavelet decomposition (à trous), Richardson-Lucy deconvolution, and unsharp masking.

---

## 3. Running the Desktop Application (Tkinter GUI)

For operators working in a desktop environment with Tkinter support (macOS, Linux with X11/Wayland desktop, or Windows):

```bash
# From the repository root:
python3 app/desktop_app.py

# Or via the macOS launcher:
./Launch_Desktop.command
```

### 3.1 Desktop GUI Capabilities
- **File Ingest**: Drag-and-drop or file-picker loading of planetary captures.
- **Dual-Limb Measurement**: Side-by-side automatic vs. human-adjusted limb outline fits to quantify operator sensitivity.
- **WinJUPOS Sensitivity**: Direct comparison with WinJUPOS manual measurement formats ($\Delta\text{sky}$ in arcseconds).
- **Video Stacker Panel**: Native Alignment Point (AP) placement, lucky imaging threshold sliders, drizzle super-resolution ($\times 1, \times 1.5, \times 2, \times 3$), and planetary derotation.

---

## 4. Running via the Unified Command-Line Interface (`cli.py`)

The CLI provides scriptable access to every stage of the pipeline. You can run it either via `python3 app/cli.py <subcommand>` or via the shortcut `./Launch_CLI.command`.

### 4.1 Ephemeris Resolution (`eph`)
Calculates the physical geometry of Jupiter for any given UTC timestamp:

```bash
python3 app/cli.py eph --utc "2026-07-14 12:00:00"
```
*Outputs Central Meridian III, distance to Earth in AU, sub-observer latitude $D_E$, North PA, and angular diameter.*

### 4.2 GRS & Galilean Moon Transit Planner (`transits`)
Finds upcoming Jovian meridian transit events and satellite shadow transits:

```bash
python3 app/cli.py transits --utc "2026-07-14 00:00:00" --days 7 --json
```

### 4.3 Measuring a Stacked Planetary Image (`process` / `measure`)
Runs the full metrology pipeline on a single image file:

```bash
python3 app/cli.py process path/to/jupiter_stack.png --utc "2026-07-14 21:45:00" --out outputs/my_measurement
```
*Creates a dedicated output folder with `SUPERDUPER_BEST_ANSWER.txt`, `publish.json`, `FULL_REPORT.txt`, and coordinate overlay plots.*

### 4.4 High-Speed Video Stacking (`video-stack`)
Aligns and stacks raw video captures (SER or uncompressed AVI):

```bash
python3 app/cli.py video-stack path/to/capture.ser \
    --keep 0.25 \
    --ap-grid 8 \
    --drizzle 2 \
    --derotate-mode hybrid \
    --out outputs/stacked_run
```

### 4.5 End-to-End One-Shot Video to Coordinates (`video-to-answer`)
Combines stacking, lucky frame selection, zonal derotation, wavelet sharpening, disk navigation, and multi-method GRS measurement in a single automated command:

```bash
python3 app/cli.py video-to-answer path/to/capture.ser --drizzle 2 --sharpen wavelet
```

### 4.6 Wavelet Sharpening & Deconvolution (`sharpen`)
Applies scale-separated à trous wavelet sharpening or Richardson-Lucy deconvolution:

```bash
python3 app/cli.py sharpen path/to/raw_stack.png --method wavelet --layers 4 --out outputs/sharpened.png
```

### 4.7 RGB Channel Combination with Derotation (`rgb-combine`)
Combines separated Red, Green, and Blue filter captures, correcting for planetary rotation between filter changes:

```bash
python3 app/cli.py rgb-combine \
    -r red_channel.png --time-r "2026-07-14 22:00:00" \
    -g green_channel.png --time-g "2026-07-14 22:02:30" \
    -b blue_channel.png --time-b "2026-07-14 22:05:00" \
    --derotate \
    --out outputs/derotated_rgb.png
```

### 4.8 Zonal Jet Stream & Cloud Drift Analysis (`wind-analysis`)
Measures cloud velocity offsets relative to System III across latitude belts:

```bash
python3 app/cli.py wind-analysis outputs/stacked_run/ --bins 30
```

### 4.9 Long-Term GRS Longitudinal Drift Modeling (`drift`)
Fits linear, quadratic, and 90-day oscillatory drift models to historical observations:

```bash
python3 app/cli.py drift --csv path/to/observations.csv --predict-days 90
```

### 4.10 Synthetic Ground-Truth Generation & Calibration (`synth` / `certify`)
Generates high-fidelity synthetic planetary images with known physics parameters and measures recovery error:

```bash
# Generate a single 1080p synthetic frame with full metrology verification:
python3 app/cli.py synth --resolution 1080p --mode metrology --seed 42

# Run an automated multi-trial certification suite:
python3 app/cli.py certify --n 10 --resolution 1080p
```

### 4.11 WinJUPOS Format Export (`jupos-export`)
Converts pipeline results into standard `.pos` text files for import into WinJUPOS:

```bash
python3 app/cli.py jupos-export outputs/my_measurement/ --out outputs/export.pos
```

---

## 5. Running the Test Suite & Benchmarks

### 5.1 Running the Pytest Suite
The repository includes a comprehensive test suite covering geometric projections, SPICE ephemerides, Lucas-Kanade alignment, C kernel numerical parity, Kalman filtering, and accuracy gates.

```bash
# Run all unit tests (excluding long synthetic end-to-end runs):
pytest -m "not slow"

# Run tests in parallel across all CPU cores:
pytest -n auto -m "not slow"

# Run specific module test suites:
pytest tests/test_cspeed.py tests/test_grs_ellipse.py tests/test_grs_drift.py
pytest tests/test_ser_io.py tests/test_wind_analysis.py tests/test_rgb_combine.py
pytest tests/test_transits.py tests/test_sharpen_lab.py tests/test_session_planner.py
```

### 5.2 Running Performance Benchmarks
To measure C kernel acceleration vs. pure NumPy/SciPy operations:

```bash
python3 tools/cspeed_benchmark.py
```

To run the full resolution vs. seeing stress test:

```bash
python3 tools/seeing_floor_stress.py
```

---

## 6. Understanding the Generated Output Files

Every measurement job writes a clean, structured bundle of files into its output directory:

| Filename | Description |
|---|---|
| `SUPERDUPER_BEST_ANSWER.txt` | **The Primary Result Card**: Clear, human-readable summary of the single recommended System III longitude, planetographic latitude, and uncertainty range. |
| `publish.json` / `publish.txt` | **Publication Metadata**: Standardized academic format containing time bounds, CM provenance, quality flags, and instrument parameters. |
| `FULL_REPORT.txt` | **Complete Metrology Breakdown**: Tabulated results from all 10+ estimators, limb fit parameters, Monte Carlo scatter, and systematic error budget. |
| `pro_ephemeris.json` | **Physical Ephemeris**: Sub-Earth point, sub-solar point, phase angle, and light-time delay at observation epoch. |
| `winjupos_compatible_measure.txt` | **WinJUPOS Format**: Formatted lines ready to copy-paste into WinJUPOS observation logs. |
| `preview.png` / `annotated_grs.png` | **Visual Overlays**: Fitted planetary limb, central meridian line, GRS bounding ellipse, and crop zooms. |

---

## 7. Troubleshooting & Common Issues

1. **`No module named 'tkinter'`**:
   - On headless Linux servers without a desktop display, GUI tests are skipped automatically. For full desktop GUI support on Linux, install: `sudo apt-get install python3-tk`.
2. **Missing C compiler on runtime**:
   - If `gcc` or `clang` is not available, the system automatically uses SciPy's `map_coordinates`. Calculations will be bit-identical but run ~3.5× slower during video stacking.
3. **SPICE Kernel Files**:
   - Bundled NAIF SPICE kernels reside in `app/ephemeris_data/spice/`. If offline, the pipeline uses local kernels; if an internet connection is available and kernels are missing, it queries the NASA JPL Horizons REST API.
4. **Port 8765 already in use**:
   - Start the web server on a custom port using: `PORT=8888 python3 app/server.py`.

---

*For an in-depth mathematical walkthrough, physical derivations, and line-level architecture breakdown of every module, please read [`docs/GRS_CODE_WALKTHROUGH_ESSAY.md`](docs/GRS_CODE_WALKTHROUGH_ESSAY.md).*
