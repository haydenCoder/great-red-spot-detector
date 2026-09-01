#!/usr/bin/env python3
"""
Great Red Spot Detector — the desktop app I built for my astrophysics coursework

Core workflow: open image → set UTC → Process (auto + by-eye limb) → publish.
SPIRE-Net weights are shipped frozen (inference only — no training UI needed).

I spent most of my time on this file — it's the thing you actually see and
interact with. The UI design went through several iterations: first it was
all dark themed (which looked cool but was hard to read), then I switched
to a light theme with black labels and grey descriptions (macOS inspired),
and finally settled on the current colour palette with per-card accent
colours so the key metrics stand out at a glance.

The biggest challenge was getting the Tkinter layout right. Tkinter isn't
great for complex layouts — no CSS, no flexbox, just pack/grid/place. I
ended up using pack() for most things with carefully nested Frames to
get the layout I wanted. It's not perfect but it works.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            exe = Path(sys.executable).resolve()
            if exe.parent.name == "MacOS":
                return exe.parents[2].parent / "GRS_Observatory_Data"
            return exe.parent
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundle_code_dir() -> Path:
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


BASE = app_base_dir()
CODE = bundle_code_dir()
sys.path.insert(0, str(CODE))

for sub in ("outputs", "uploads", "ssd_cache", "nasa_cache", "logs", "ephemeris_data", "models"):
    (BASE / sub).mkdir(parents=True, exist_ok=True)


def resolve_manual_path(code: Path, base: Path) -> Optional[Path]:
    """Locate the user guide — tries several possible locations.

    Pure helper, no Tk required. This exists because the guide file
    ends up in different places depending on whether we're running
    from source, from a PyInstaller bundle, or from the repo root.
    """
    cands = [
        code.parent / "docs" / "GRS_CODE_WALKTHROUGH_ESSAY.md",
        code / "docs" / "GRS_CODE_WALKTHROUGH_ESSAY.md",
        base / "docs" / "GRS_CODE_WALKTHROUGH_ESSAY.md",
        code.parent / "docs" / "GRS_CODE_WALKTHROUGH_ESSAY.html",
        base / "docs" / "GRS_CODE_WALKTHROUGH_ESSAY.html",
    ]
    for p in cands:
        if p.exists():
            return p
    return None


def resolve_buttons_doc_path(code: Path, base: Path) -> Optional[Path]:
    """
    Locate the button → function guide.

    Prefers dedicated HTML, then the book / desktop reference docs.
    Pure helper — no Tk required (unit-testable). Same story as
    resolve_manual_path: the guide moves around depending on how
    you run the app, so we search a bunch of candidate paths.
    """
    names = (
        "BUTTON_GUIDE.html",
        "button_guide.html",
        "BUTTONS.html",
        "features/button_guide.html",
        "features/BUTTON_GUIDE.html",
        "GRS_CODE_WALKTHROUGH_ESSAY.html",
        "GRS_CODE_WALKTHROUGH_ESSAY.md",
        "reference/mod_desktop_app.md",
        "reference/01_FEATURES.md",
    )
    roots = (code.parent / "docs", code / "docs", base / "docs")
    for root in roots:
        for name in names:
            p = root / name
            if p.exists():
                return p
    return None


def open_local_doc(path: Path) -> None:
    """Open a local doc with the OS default handler (no Tk)."""
    import subprocess
    import webbrowser

    resolved = path.resolve()
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(resolved)])
    else:
        webbrowser.open(resolved.as_uri())

os.environ.setdefault("GRS_RAM_GB", "16")
os.environ.setdefault("GRS_SSD_CACHE", str(BASE / "ssd_cache"))

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
    _HAS_TK = True
except ImportError:
    tk = None
    ttk = filedialog = messagebox = scrolledtext = None
    _HAS_TK = False

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

from verbose_log import CONSOLE
import numpy as np

try:
    import ram_ssd
    ram_ssd.SSD_CACHE = BASE / "ssd_cache"
    ram_ssd.SSD_CACHE.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
try:
    import nasa_compare
    nasa_compare.CACHE = BASE / "nasa_cache"
    nasa_compare.CACHE.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
try:
    import ephemeris_pro
    ephemeris_pro.CACHE = BASE / "nasa_cache"
    ephemeris_pro.EPH_DIR = BASE / "ephemeris_data"
    ephemeris_pro.CACHE.mkdir(parents=True, exist_ok=True)
    ephemeris_pro.EPH_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


# Light, readable UI — refined colour palette inspired by macOS design language
BG = "#eef0f5"          # app background (cool off-white)
PANEL = "#ffffff"       # side panels (clean white)
PANEL2 = "#f4f6fa"      # secondary surfaces (subtle elevation)
CARD = "#ffffff"        # metric cards (white)
CARD_HEADER = "#f0f4ff" # metric card header tint (light blue hint)
FG = "#0f172a"          # primary text (near-black, deep navy)
MUTED = "#64748b"       # secondary / descriptions (blue-grey)
ACCENT = "#1d4ed8"      # primary actions (royal blue)
ACCENT_HOVER = "#2563eb" # hover variant
OK = "#15803d"          # success green
WARN = "#b45309"        # warning amber (richer, more legible)
ERR = "#b91c1c"         # error red
PURPLE = "#7c3aed"      # factory / specialist
BORDER = "#cbd5e1"      # borders (blue-grey, softer)
BORDER_FOCUS = "#1d4ed8" # focus ring border
INPUT_BG = "#ffffff"    # input fields
CONSOLE_BG = "#0f172a"  # console dark (Slate 900)
CONSOLE_FG = "#e2e8f0"  # console light text (Slate 200)
BTN_TEXT = "#ffffff"    # text on coloured buttons
SHADOW_BG = "#94a3b8"   # shadow / inactive indicators

# Accurate plain-language help for every control / action (shown via ⓘ)
HELP: Dict[str, str] = {
    "app": (
        "Jupiter Great Red Spot Detector measures the Great Red Spot\n"
        "in System III longitude and latitude, with calibrated uncertainties.\n\n"
        "• Synthetic = fake planet image with known truth (tests accuracy).\n"
        "• Process = measure a real FITS/SER/PNG.\n"
        "• Results include lon/lat, σ in arcseconds, and full JSON packages.\n\n"
        "Ground-based optical GRS metrology (limb + map + colour lock + SPICE)."
    ),
    "time": (
        "Observation time (UTC) for REAL data only.\n\n"
        "Used when you Process a file or Resolve Ephemeris, so System III geometry\n"
        "matches your night.\n\n"
        "NOT used for Synthetic — synthetic always picks a random UTC epoch."
    ),
    "time_error": (
        "How uncertain the observation time is (seconds).\n\n"
        "Jupiter rotates ~36°/hour in System III, so timing error becomes\n"
        "longitude uncertainty in the formal error budget."
    ),
    "region": (
        "Framing for SYNTHETIC images only:\n"
        "• global / full_disk — whole planet\n"
        "• grs_closeup — zoomed on the GRS\n"
        "• se_belt — southern equatorial belt band\n"
        "• equatorial — equator-centered crop\n\n"
        "Does not change Process of real files."
    ),
    "aperture": (
        "Telescope diameter in metres (e.g. 0.35 = 14-inch class).\n\n"
        "Only used to quote a diffraction floor (λ/D) in the report.\n"
        "It does not change the measured lon/lat algorithm."
    ),
    "cm": (
        "Force Jupiter’s central meridian (System III) longitude.\n\n"
        "Paste a value from WinJUPOS / SPICE when you need absolute System III.\n"
        "Leave empty to use Horizons / analytical CM.\n\n"
        "Critical for absolute longitude; less important for pure relative tests."
    ),
    "sublat": (
        "Sub-observer latitude: how tilted Jupiter appears from Earth (°).\n\n"
        "Affects the oriented projection used for lon/lat.\n"
        "Leave empty unless you have a trusted ephemeris value."
    ),
    "pa": (
        "North pole position angle on the sky (degrees east of north).\n\n"
        "Rotates the geometric model so “up” matches the image.\n"
        "Leave empty unless known from ephemeris."
    ),
    "horizons": (
        "Contact NASA JPL Horizons for Jupiter distance / orientation when online.\n\n"
        "Geometry context only — Horizons is NOT an official GRS longitude product."
    ),
    "spice": (
        "If spiceypy + kernel files are installed, use professional SPICE geometry.\n\n"
        "Optional. Without kernels this does nothing harmful."
    ),
    "winjupos": (
        "Load a WinJUPOS/JUPOS CML table (CSV/JSON of time + System III CM).\n\n"
        "Interpolated to your process time for absolute CM.\n"
        "Best practice for publishable absolute longitudes."
    ),
    "resolution": (
        "Pixel size of GENERATED synthetic images:\n"
        "• 1080p — fast tests\n"
        "• 4K — good default balance\n"
        "• 8K — high detail, slower / more RAM\n"
        "• 16K — maximum (may step down if RAM is tight)\n"
        "• auto — pick largest safe size for ~16 GB\n\n"
        "Does not change the size of a real file you Process."
    ),
    "mc": (
        "Hierarchical Monte Carlo iterations.\n\n"
        "Re-runs the measurement with map noise, limb/nav jitter, template scale,\n"
        "and CM/time priors to estimate RANDOM uncertainty (σ).\n\n"
        "Higher = more stable σ but slower. Typical 40–80."
    ),
    "inj": (
        "Phase-reference injection trials.\n\n"
        "Injects known dark ovals into the image, recovers them with the same\n"
        "science pipeline, estimates BIAS and scatter, then (if physical)\n"
        "bias-corrects the real GRS position.\n\n"
        "Higher = better calibration, slower. Typical 16–32."
    ),
    "vlbi": (
        "Use the advanced multi-method measurement stack:\n"
        "multi-scale template match, oriented cylindrical map, multi-definition\n"
        "systematics, hierarchical MC, formal error budget.\n\n"
        "Recommended ON for science-quality results."
    ),
    "factory_heavy": (
        "Factory heavy mode: more injection trials and heavier MC when fidelity\n"
        "toggles request it. Slower; better calibration on synthetics."
    ),
    "nasa": (
        "After measuring, write a Horizons Jupiter geometry report (CM, distance — not GRS lon).\n\n"
        "Useful context only. Offline GRS lon model is schematic — on synthetics\n"
        "trust truth_recovery arcseconds, not the schematic lon delta."
    ),
    "nn": (
        "SPIRE-Net: a small CNN that suggests a rough GRS position.\n\n"
        "Used only as a SOFT PRIOR — lightly blended if it agrees with physics\n"
        "methods; ignored if it disagrees. Not the main measurer."
    ),
    "imaging": (
        "When processing real files, try the full imaging branch first\n"
        "(ingest / channels / stack-like path from grs_complete_system).\n\n"
        "If it fails, processing continues on the raw frame."
    ),
    "synth_measure": (
        "After generating a synthetic planet, immediately run the full GRS measure\n"
        "and score truth recovery (how many arcsec off known truth)."
    ),
    "factory_hard": (
        "When running Factory Night, also run the hard-synth stress suite\n"
        "(mismatch physics: wrong CM, blur, noise) to check error-bar coverage."
    ),
    "btn_synth": (
        "GENERATE SYNTHETIC\n\n"
        "Creates a high-quality fake Jupiter + GRS image.\n"
        "Always picks a RANDOM UTC observation time (you are not asked).\n"
        "If “Measure after synthetic” is on, runs the full advanced measure and\n"
        "reports truth recovery in arcseconds.\n\n"
        "Best for testing the pipeline without real data."
    ),
    "btn_open": (
        "OPEN FILE\n\n"
        "Load a real FITS, SER, PNG, or JPEG for Process.\n"
        "Does not measure until you click Process."
    ),
    "btn_process": (
        "PROCESS FILE (FULL ADVANCED STACK)\n\n"
        "Measures GRS on your loaded image using:\n"
        "1) optional imaging branch\n"
        "2) pro ephemeris (Horizons/SPICE/WinJUPOS/overrides)\n"
        "3) limb navigation\n"
        "4) VLBI multi-scale correlator + multi-method consensus\n"
        "5) phase-ref injection bias calibration\n"
        "6) hierarchical Monte Carlo\n"
        "7) filter closure / definitions\n"
        "8) SPIRE-Net soft prior (if enabled)\n"
        "9) NASA geometry compare (if enabled)\n"
        "10) full job_result JSON package\n\n"
        "Requires: file + observation time."
    ),
    "btn_eph": (
        "RESOLVE EPHEMERIS\n\n"
        "Computes Jupiter geometry at the session time only\n"
        "(CM III, distance, sub-lat, NP PA when available).\n"
        "Does not measure the GRS on an image."
    ),
    "btn_multi": (
        "MULTI-EPOCH DIFFERENTIALS\n\n"
        "Scans previous job results in the outputs folder.\n"
        "Builds night-to-night Δlon/Δlat relative to a reference epoch\n"
        "(common-mode errors partly cancel), fits drift °/day, optional RTS smooth.\n\n"
        "Needs ≥2 prior measured epochs (run synthetic/process a few times first)."
    ),
    "btn_hard": (
        "HARD-SYNTH STRESS SUITE\n\n"
        "Generates a base synthetic, then stresses it (wrong CM, seeing, noise,\n"
        "orientation) and checks whether reported error bars still cover truth.\n\n"
        "This calibrates honesty of σ — it is not a single science longitude."
    ),
    "btn_factory": (
        "FACTORY NIGHT (ALL PILLARS)\n\n"
        "One-button end-to-end self-test:\n"
        "1) Pro ephemeris (session time for geometry context)\n"
        "2) Synthetic with RANDOM epoch + full advanced measure\n"
        "3) Multi-epoch scan of all outputs\n"
        "4) Optional hard-synth suite\n\n"
        "Writes a factory_night report under outputs/."
    ),
    "btn_nn": (
        "TRAIN SPIRE-NET\n\n"
        "Trains the small CNN on synthetic labeled maps so the soft prior\n"
        "is slightly better. Physics methods remain authoritative."
    ),
    "btn_outputs": (
        "Opens the outputs folder in Finder (PNG, FITS, JSON reports)."
    ),
    "btn_save": (
        "Save the last full result package as a JSON file you choose."
    ),
    "btn_clear": (
        "Clear the live console log in this window."
    ),
    "metrics": (
        "Top strip after a job:\n"
        "• Grade — quality label of the metrology run\n"
        "• Lon III / Lat — bias-corrected GRS position\n"
        "• σ_tot — total sky uncertainty (arcsec)\n"
        "• Truth rec — synthetic only: error vs known truth (arcsec)\n"
        "• Epoch — observation time used for that run"
    ),
}


class LogBridge:
    def __init__(self, q: queue.Queue):
        self.q = q
        self._last = 0

    def poll(self):
        try:
            for ln in CONSOLE.since(self._last):
                self._last = max(self._last, ln.get("id", self._last))
                self.q.put(("log", ln))
        except Exception:
            pass


class GRSDesktopApp(tk.Tk if _HAS_TK else object):
    def __init__(self):
        super().__init__()
        self.title("Jupiter Great Red Spot Detector · System III Metrology")
        self.geometry("1440x900")
        self.minsize(1100, 700)
        self.configure(bg=BG)

        self.msg_q: queue.Queue = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.busy = False
        self.file_path: Optional[Path] = None
        self.winjupos_path: Optional[str] = None
        self._photo = None
        self.last_package: Optional[Dict[str, Any]] = None
        self.log_bridge = LogBridge(self.msg_q)
        self._license_status = None
        try:
            import license_manager as _lic
            self._license_status = _lic.load_status(BASE)
        except Exception:
            pass

        self._build_style()
        self._build_menu()
        self._build_ui()
        self.after(150, self._tick)
        # Portable CNN + transparent group access log
        try:
            from paths import ensure_tree, ensure_models_present
            tree = ensure_tree()
            ensure_models_present()
            CONSOLE.info(f"Models ready: {tree.get('models')}")
        except Exception as e:
            CONSOLE.warn(f"Model path setup: {e}")
        try:
            import group_access
            group_access.log_event("app_start", {"base": str(BASE)})
            CONSOLE.info(group_access.logging_enabled_message())
        except Exception:
            pass
        CONSOLE.ok(f"Desktop ready · {BASE}")
        if self._license_status is not None:
            ls = self._license_status
            CONSOLE.info(
                f"License: {ls.plan_label} · valid={ls.valid} · licensed={ls.licensed}"
            )
        CONSOLE.info("Synthetic = always random epoch · Process = full advanced stack")

    # ── menu (License / Help) ──────────────────────────────────────────
    def _build_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)
        m_lic = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="License", menu=m_lic)
        m_lic.add_command(label="Status…", command=self._license_show)
        m_lic.add_command(label="Activate key…", command=self._license_activate)
        m_lic.add_command(label="Copy machine ID", command=self._license_copy_machine)
        m_help = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=m_help)
        m_help.add_command(label="The Book (only guide)…", command=self._open_manual)
        m_help.add_separator()
        m_help.add_command(label="About", command=self._about)

    def _license_show(self):
        try:
            import license_manager as lic
            st = lic.load_status(BASE)
            self._license_status = st
            messagebox.showinfo(
                "License status",
                f"Plan: {st.plan_label}\n"
                f"Valid: {st.valid}\n"
                f"Licensed: {st.licensed}\n"
                f"Customer: {st.customer or '—'}\n"
                f"Expires: {st.expires_utc or 'none'}\n"
                f"Commercial use: {st.features.get('commercial_use', False)}\n"
                f"Machine: {lic.machine_fingerprint()}\n\n"
                f"{st.message}",
            )
        except Exception as e:
            messagebox.showerror("License", str(e))

    def _license_activate(self):
        try:
            import license_manager as lic
            from tkinter import simpledialog
            key = simpledialog.askstring("Activate license", "Paste your GRS-1-… license key:")
            if not key:
                return
            st = lic.save_license(BASE, key)
            self._license_status = st
            if st.valid and st.licensed:
                messagebox.showinfo("License", f"Activated: {st.plan_label}")
                CONSOLE.ok(f"License activated: {st.plan_label}")
            else:
                messagebox.showerror("License", st.message or "Activation failed")
            self._refresh_license_badge()
        except Exception as e:
            messagebox.showerror("License", str(e))

    def _license_copy_machine(self):
        try:
            import license_manager as lic
            mid = lic.machine_fingerprint()
            self.clipboard_clear()
            self.clipboard_append(mid)
            messagebox.showinfo("Machine ID", f"Copied to clipboard:\n{mid}")
        except Exception as e:
            messagebox.showerror("Machine ID", str(e))

    def _manual_path(self) -> Optional[Path]:
        """Only user guide: GRS_CODE_WALKTHROUGH_ESSAY.md"""
        return resolve_manual_path(CODE, BASE)

    def _buttons_doc_path(self) -> Optional[Path]:
        """Button → function guide (HTML preferred, then book / features docs)."""
        return resolve_buttons_doc_path(CODE, BASE)

    def _open_path_in_viewer(self, path: Path) -> None:
        """Open a local file with the OS default viewer / browser."""
        open_local_doc(path)

    def _open_manual(self):
        p = self._manual_path()
        if p is None:
            messagebox.showinfo(
                "The Book",
                "Guide not found.\nExpected: docs/GRS_CODE_WALKTHROUGH_ESSAY.md",
            )
            return
        try:
            self._open_path_in_viewer(p)
        except Exception as e:
            messagebox.showerror("The Book", f"Could not open guide:\n{e}")

    def _open_buttons_doc(self):
        """Open the button guide (HTML/MD) or report clearly if missing."""
        p = self._buttons_doc_path()
        if p is None:
            messagebox.showinfo(
                "Button guide",
                "Button guide not found.\n"
                "Expected one of:\n"
                "  docs/BUTTON_GUIDE.html\n"
                "  docs/features/button_guide.html\n"
                "  docs/GRS_CODE_WALKTHROUGH_ESSAY.md\n"
                "  docs/reference/mod_desktop_app.md",
            )
            return
        try:
            self._open_path_in_viewer(p)
        except Exception as e:
            messagebox.showerror("Button guide", f"Could not open guide:\n{e}")

    def _about(self):
        try:
            from product_core import PRODUCT_NAME, PRODUCT_VERSION, PRODUCT_TAGLINE
            ver = PRODUCT_VERSION
            name = PRODUCT_NAME
            tag = PRODUCT_TAGLINE
        except Exception:
            # Read VERSION rather than hardcoding: literal fallbacks go stale.
            ver = "unknown"
            try:
                _vp = Path(__file__).resolve().parent.parent / "VERSION"
                if _vp.exists():
                    ver = _vp.read_text(encoding="utf-8").strip() or "unknown"
            except Exception:
                pass
            name, tag = "Jupiter Great Red Spot Detector", "Optical GRS metrology"
        lic_line = ""
        if self._license_status:
            lic_line = f"\nLicense: {self._license_status.plan_label}"
        messagebox.showinfo(
            "About",
            f"{name} v{ver}\n{tag}{lic_line}\n\n"
            "Ground-based optical metrology for Jupiter’s Great Red Spot.\n"
            "Publish: GS-MAP · CM: SPICE / Horizons / WinJUPOS\n"
            "Only guide: docs/GRS_CODE_WALKTHROUGH_ESSAY.md\n"
            "CNN weights: app/models/spire_net_weights.npz\n"
            "© 2026 — see LICENSE.",
        )

    def _refresh_license_badge(self):
        if not hasattr(self, "license_var"):
            return
        st = self._license_status
        if st is None:
            self.license_var.set("License: —")
            return
        if st.licensed:
            self.license_var.set(f"● {st.plan_label}")
        else:
            self.license_var.set("● Evaluation")

    # ── style ──────────────────────────────────────────────────────────
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        # Refined light theme: deep navy text, blue-grey secondary, clean white panels
        style.configure(".", background=BG, foreground=FG, fieldbackground=INPUT_BG)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG, font=("Helvetica", 12))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Helvetica", 11))
        style.configure("Card.TLabel", background=PANEL, foreground=FG, font=("Helvetica", 12))
        style.configure("TButton", font=("Helvetica", 12, "bold"), padding=10)
        style.configure(
            "TCombobox",
            fieldbackground=INPUT_BG,
            background=PANEL2,
            foreground=FG,
            arrowcolor=FG,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=6,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", INPUT_BG)],
            foreground=[("readonly", FG)],
            selectbackground=[("readonly", ACCENT)],
            selectforeground=[("readonly", BTN_TEXT)],
        )
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=PANEL2,
            foreground=MUTED,
            padding=[18, 10],
            font=("Helvetica", 12, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT)],
            foreground=[("selected", BTN_TEXT)],
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#d1d5db",
            background=ACCENT,
            thickness=6,
            bordercolor=BORDER,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )
        style.configure(
            "Vertical.TScrollbar",
            background=PANEL2,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=FG,
        )

    # ── UI ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header bar — top-of-app brand strip
        head = tk.Frame(self, bg=BG, highlightthickness=0)
        head.pack(fill=tk.X, padx=18, pady=(16, 10))
        left_h = tk.Frame(head, bg=BG)
        left_h.pack(side=tk.LEFT, fill=tk.X, expand=True)
        title_row = tk.Frame(left_h, bg=BG)
        title_row.pack(anchor=tk.W)
        # Jupiter symbol logo badge
        logo = tk.Label(
            title_row, text=" ♃ ", bg=ACCENT, fg=BTN_TEXT,
            font=("Helvetica", 16, "bold"), padx=6, pady=6,
            highlightbackground=ACCENT, highlightthickness=0,
        )
        logo.pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(
            title_row, text="Jupiter Great Red Spot Detector", bg=BG, fg=FG,
            font=("Helvetica", 22, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            left_h,
            text="Ground-based optical metrology · System III longitude & latitude · publish-ready packages",
            bg=BG, fg=MUTED, font=("Helvetica", 11),
        ).pack(anchor=tk.W, pady=(3, 0))

        right_h = tk.Frame(head, bg=BG)
        right_h.pack(side=tk.RIGHT)
        self.license_var = tk.StringVar(value="● Evaluation")
        self.license_lbl = tk.Label(
            right_h, textvariable=self.license_var, bg=PANEL, fg=ACCENT,
            font=("Helvetica", 11, "bold"), padx=14, pady=7,
            highlightbackground=BORDER, highlightthickness=1,
        )
        self.license_lbl.pack(side=tk.RIGHT, padx=6)
        self.status_var = tk.StringVar(value="● IDLE")
        self.status_lbl = tk.Label(
            right_h, textvariable=self.status_var, bg=PANEL, fg=SHADOW_BG,
            font=("Helvetica", 12, "bold"), padx=16, pady=7,
            highlightbackground=BORDER, highlightthickness=1,
        )
        self.status_lbl.pack(side=tk.RIGHT, padx=6)
        self.prog = ttk.Progressbar(right_h, mode="indeterminate", length=120)
        self.prog.pack(side=tk.RIGHT, padx=10)
        self._refresh_license_badge()

        # Siril-style 3-tab top panel: Stacking / Derotate / Process  (v6.6.5)
        self._build_top_panel()

        body = tk.Frame(self, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=6)

        # ── Left scrollable controls (wider, web-like card) ──
        left_outer = tk.Frame(
            body, bg=PANEL, width=380,
            highlightbackground=BORDER, highlightthickness=2,
        )
        left_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_outer.pack_propagate(False)

        canvas = tk.Canvas(left_outer, bg=PANEL, highlightthickness=0, width=360)
        sb = ttk.Scrollbar(left_outer, orient=tk.VERTICAL, command=canvas.yview)
        left = tk.Frame(canvas, bg=PANEL)
        left.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=left, anchor=tk.NW, width=360)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2, pady=2)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _wheel)

        # Session
        self._section(left, "1 · Session (for real Process / Ephemeris)")
        # Empty by default — must set real mid-exposure UTC for Process (no silent now)
        self.time_var = tk.StringVar(value="")
        self.err_var = tk.StringVar(value="0")
        self.region_var = tk.StringVar(value="global")
        self.country_var = tk.StringVar(value="UTC")
        self.aperture_var = tk.StringVar(value="0.35")  # backend default only (hidden from UI)
        self._labeled_entry(
            left, "Observation time UTC (required for Process)", self.time_var,
            "Mid-exposure UTC of your real photo, e.g. 2026-01-10 15:39:26. Empty = try FITS DATE-OBS. Never use wall-clock 'now'.",
        )
        self._labeled_entry(
            left, "Time error (s)", self.err_var,
            "How uncertain the clock is. Adds longitude uncertainty.",
        )
        self._labeled_combo(
            left, "Your country (timezone clarity)", self.country_var,
            ["UTC", "US", "CA", "GB", "FR", "DE", "AU", "NZ", "JP", "CN", "IN", "BR", "ZA"],
            "Where you are observing. Helps keep local clock vs UTC clear. Ephemeris uses UTC.",
        )
        self._labeled_combo(
            left, "Synthetic framing (fake images only)", self.region_var,
            ["global", "grs_closeup", "se_belt", "equatorial", "full_disk"],
            "Only changes how the SYNTHETIC planet is framed — not real FITS.",
        )

        # Geometry overrides
        self._section(left, "2 · Absolute geometry (Process)")
        self.cm_var = tk.StringVar(value="")
        self.sublat_var = tk.StringVar(value="")
        self.pa_var = tk.StringVar(value="")
        self._labeled_entry(
            left, "CM III override (optional)", self.cm_var,
            "Paste central meridian from WinJUPOS if you have it. Leave blank to auto.",
        )
        self._labeled_entry(
            left, "Sub-lat override", self.sublat_var,
            "Optional sub-observer latitude. Leave blank unless you know it.",
        )
        self._labeled_entry(
            left, "North PA override", self.pa_var,
            "Optional north position angle on the sky. Usually leave blank.",
        )
        self.horizons_var = tk.BooleanVar(value=True)
        self.spice_var = tk.BooleanVar(value=True)
        self._check(left, "JPL Horizons geometry", self.horizons_var,
                    "Use NASA Horizons online for Jupiter distance/orientation.")
        self._check(left, "SPICE kernels (bundled local only)", self.spice_var,
                    "Use shipped SPICE kernels for CM/distance. No online download.")
        self.winjupos_path = None  # CM table upload removed — use CM override or WJ paste
        self.wj_lbl = tk.Label(
            left, text="CM table download/upload removed. Use CM override or paste WJ GRS.",
            bg=PANEL, fg=MUTED, font=("Helvetica", 11), wraplength=340, justify=tk.LEFT,
        )
        self.wj_lbl.pack(anchor=tk.W, padx=14, pady=(0, 6))
        self.wj_lon_var = tk.StringVar(value="")
        self.wj_lat_var = tk.StringVar(value="")
        self._labeled_entry(
            left, "WinJUPOS GRS lon (optional check)", self.wj_lon_var,
            "Paste lon from WinJUPOS to compare Δsky. Not required for Process.",
        )
        self._labeled_entry(
            left, "WinJUPOS GRS lat (optional check)", self.wj_lat_var,
            "Paste lat from WinJUPOS (prefer planetographic). Same core definition.",
        )

        # Metrology knobs
        self._section(left, "3 · Measurement quality knobs")
        self.res_var = tk.StringVar(value="4K")
        self.mc_var = tk.StringVar(value="60")
        self.inj_var = tk.StringVar(value="28")
        self._labeled_combo(
            left, "Synthetic resolution", self.res_var,
            ["1080p", "4K", "8K", "16K", "auto"],
            "Pixel size of fake planets only. Higher = slower.",
        )
        self._labeled_entry(
            left, "MC iterations (random σ)", self.mc_var,
            "How many random trials for uncertainty. More = slower, stabler σ.",
        )
        self._labeled_entry(
            left, "Injection trials (bias cal)", self.inj_var,
            "Fake probe injections to estimate measurement bias.",
        )
        # Fixed defaults for the simplified UI (I removed train/factory/hard-synth to keep it clean)
        self.vlbi_var = tk.BooleanVar(value=True)       # advanced multi-method stack
        self.factory_var = tk.BooleanVar(value=False)
        self.nasa_var = tk.BooleanVar(value=True)       # geometry report only
        self.nn_var = tk.BooleanVar(value=True)  # CNN prior ON
        self.nn_epochs_var = tk.StringVar(value="25")
        self.nn_samples_var = tk.StringVar(value="16")
        self.nn_lr_var = tk.StringVar(value="0.01")
        self.nn_hours_var = tk.StringVar(value="8")
        self.nn_cache_var = tk.StringVar(value="128")
        self.nn_overnight_var = tk.BooleanVar(value=False)
        self.nn_resume_var = tk.BooleanVar(value=True)
        self.imaging_var = tk.BooleanVar(value=False)
        self.synth_process_var = tk.BooleanVar(value=False)
        self.hard_in_factory_var = tk.BooleanVar(value=False)
        self.dual_var = tk.BooleanVar(value=True)
        self._check(left, "Full multi-method stack + error budget", self.vlbi_var,
                    "Multi-method optical measure.")
        self._check(left, "Write Horizons geometry report", self.nasa_var,
                    "Planet geometry only — not an official NASA GRS longitude.")
        self._check(left, "SPIRE-Net CNN prior (weights ON)", self.nn_var,
                    "Uses app/models/spire_net_weights.npz as a soft hint. "
                    "Train with Train_SPIRE_Background.command or the train buttons below.")

        self._section(left, "4 · Process (main)")
        self._action_btn(
            left, "Open FITS / SER / PNG…", self.on_open_file,
            "Pick your telescope stack. Measurement starts only after Process.",
            secondary=True,
        )
        self.file_lbl = tk.Label(
            left, text="No file loaded", bg=PANEL, fg=MUTED,
            font=("Helvetica", 11), wraplength=340, justify=tk.LEFT,
        )
        self.file_lbl.pack(anchor=tk.W, padx=14, pady=2)
        self._action_btn(
            left, "▶  Process full (auto limb + by-eye limb)", self.on_process,
            "Main action: auto measure + by-eye cyan limb + GS-ORANGE/GS-MAP publish. "
            "UTC from file name if blank. Dual limb dialog always opens.",
            color=ACCENT,
        )
        self._action_btn(
            left, "Resolve Ephemeris only", self.on_ephemeris,
            "CM III / distance from SPICE (+ Horizons if enabled). No GRS measure.",
            secondary=True,
        )

        self._section(left, "5 · Results")
        self.nn_lbl = tk.Label(
            left, text="SPIRE-Net: ON · weights + checkpoint restored under app/models/",
            bg=PANEL, fg=MUTED, font=("Helvetica", 11), wraplength=340, justify=tk.LEFT,
        )
        self.nn_lbl.pack(anchor=tk.W, padx=14, pady=2)
        self.nn_gain_lbl = tk.Label(
            left, text="",
            bg=PANEL, fg=FG, font=("Helvetica", 11), wraplength=340, justify=tk.LEFT,
        )
        self.nn_gain_lbl.pack(anchor=tk.W, padx=14, pady=(0, 4))
        self._action_btn(
            left, "Train SPIRE-Net (quick / overnight)", self.on_nn_train,
            "Fine-tune CNN on synthetic maps. Resumes from spire_train_checkpoint.json when overnight+resume.",
            secondary=True,
        )
        self._action_btn(
            left, "Stop SPIRE-Net train", self.on_nn_stop,
            "Ask training to stop after the current step and save weights.",
            secondary=True,
        )
        self._action_btn(
            left, "🧠  Fine-Tune SPIRE-Net CNN (short)", self.on_nn_finetune,
            "Short fine-tune pass (32 samples × 6 epochs, lr=0.003) over synthetic maps. "
            "Writes app/models/spire_net_finetuned.npz — does NOT overwrite the shipped weights.",
            secondary=True,
        )
        self._action_btn(
            left, "🪐  Planetary Derotation (WinJUPOS-style)", self.on_planetary_derotate,
            "Re-runs the published measurement on the current frame with WinJUPOS-style "
            "zonal derotation (uses the current CML as the reference).",
            secondary=True,
        )
        self._action_btn(
            left, "⚡  5D Velocity AP-Grid Stack", self.on_stack_5d,
            "Experimental: multi-point AP-grid stacker with per-AP velocity tracking + "
            "zonal derotation. Operates on the synthetic video generated on the fly.",
            secondary=True,
        )
        self._action_btn(
            left, "🌟  10D Quantum-Optical Hypertensor Stack", self.on_stack_10d,
            "Experimental: 10-D bookkeeping extension (Zernike + Kolmogorov C_n²). "
            "Same numerical work as the 5D stack, with extra per-AP diagnostics.",
            secondary=True,
        )
        self._action_btn(
            left, "🌌  Infinite-D Hilbert-Space Hyper-Stack", self.on_stack_inf,
            "Experimental: path-integral-style stacker with Kolmogorov-prior + "
            "Dirichlet importance sampling. Formulation only — no quantum state.",
            secondary=True,
        )
        self._action_btn(
            left, "Open outputs folder", self.on_open_outputs,
            "Job folders with publish.txt / SUPERDUPER_BEST_ANSWER.txt.",
            secondary=True,
        )
        self._action_btn(
            left, "Save full results JSON…", self.on_save_results,
            "Export the last job package as JSON.",
            secondary=True,
        )
        self._action_btn(
            left, "Clear console", self.on_clear,
            "Clear live log only — does not delete jobs.",
            secondary=True,
        )
        tk.Frame(left, bg=PANEL, height=20).pack()

        # ── Center notebook ──
        center = tk.Frame(body, bg=BG)
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Metric strip — key result cards at a glance
        metrics = tk.Frame(center, bg=BG)
        metrics.pack(fill=tk.X, pady=(0, 10))
        self.metric_vars = {}
        mhead = tk.Frame(metrics, bg=BG)
        mhead.pack(fill=tk.X)
        tk.Label(
            mhead, text="KEY METRICS", bg=BG, fg=FG,
            font=("Helvetica", 11, "bold"),
        ).pack(side=tk.LEFT)
        mrow = tk.Frame(metrics, bg=BG)
        mrow.pack(fill=tk.X, pady=(6, 0))
        metric_colours = {
            "grade": (ACCENT, "Grade"),
            "lon":   (FG,    "Lon III"),
            "lat":   (FG,    "Lat"),
            "sigma": (WARN,  "σ_tot ″"),
            "truth": (OK,    "Truth ″"),
            "epoch": (MUTED, "Epoch"),
        }
        for key, (val_colour, label) in metric_colours.items():
            card = tk.Frame(
                mrow, bg=CARD,
                highlightbackground=BORDER, highlightthickness=1,
            )
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)
            # Header row of card (light blue tint)
            hdr = tk.Frame(card, bg=CARD_HEADER)
            hdr.pack(fill=tk.X)
            tk.Label(
                hdr, text=label.upper(), bg=CARD_HEADER, fg=ACCENT,
                font=("Helvetica", 9, "bold"),
            ).pack(anchor=tk.W, padx=10, pady=(5, 3))
            # Value row
            v = tk.StringVar(value="—")
            self.metric_vars[key] = v
            tk.Label(
                card, textvariable=v, bg=CARD, fg=val_colour,
                font=("Menlo", 14, "bold"),
            ).pack(anchor=tk.W, padx=10, pady=(2, 8))

        self.nb = ttk.Notebook(center)
        self.nb.pack(fill=tk.BOTH, expand=True)

        # Preview tab
        tab_prev = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(tab_prev, text="  Preview  ")
        self.preview_lbl = tk.Label(
            tab_prev,
            text="No image yet\n\nOpen a FITS/SER/PNG file  or  Generate Synthetic",
            bg=PANEL2, fg=MUTED, font=("Helvetica", 14),
            highlightbackground=BORDER, highlightthickness=1,
        )
        self.preview_lbl.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Dashboard first — small table
        tab_dash = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(tab_dash, text="  Dashboard  ")
        self.dash = scrolledtext.ScrolledText(
            tab_dash, bg=CONSOLE_BG, fg=CONSOLE_FG, font=("Menlo", 13),
            wrap=tk.NONE, relief=tk.FLAT, padx=10, pady=10,
        )
        self.dash.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.dash.insert(
            tk.END,
            "DASHBOARD (small table)\n"
            "Run Process — key numbers appear here.\n"
            "Full Results tab has the complete dump.\n",
        )

        # Full Results — everything
        tab_res = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(tab_res, text="  Full Results  ")
        self.results = scrolledtext.ScrolledText(
            tab_res, bg=CONSOLE_BG, fg=CONSOLE_FG, insertbackground=CONSOLE_FG,
            font=("Menlo", 11), wrap=tk.WORD, relief=tk.FLAT, borderwidth=0,
            highlightthickness=0, padx=8, pady=8,
        )
        self.results.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.results.insert(tk.END, "Full Results: complete report after Process.\n")

        # ── Console (right panel) ──
        right = tk.Frame(
            body, bg=PANEL, width=340,
            highlightbackground=BORDER, highlightthickness=1,
        )
        right.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(10, 0))
        right.pack_propagate(False)
        ch = tk.Frame(right, bg=PANEL)
        ch.pack(fill=tk.X, padx=10, pady=(10, 4))
        tk.Label(
            ch, text="LIVE LOG", bg=PANEL, fg=ACCENT,
            font=("Helvetica", 11, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            ch, text="auto-scroll ↓", bg=PANEL, fg=MUTED,
            font=("Helvetica", 9),
        ).pack(side=tk.RIGHT)
        self.console = scrolledtext.ScrolledText(
            right, bg=CONSOLE_BG, fg=CONSOLE_FG, font=("Menlo", 11),
            wrap=tk.WORD, relief=tk.FLAT, padx=6, pady=6,
        )
        self.console.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        for tag, color in (
            ("OK", "#4ade80"), ("WARN", "#fbbf24"), ("ERROR", "#f87171"),
            ("INFO", CONSOLE_FG), ("DEBUG", "#93c5fd"),
        ):
            self.console.tag_config(tag, foreground=color)

        # Footer status bar
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill=tk.X, padx=18, pady=(6, 10))
        tk.Label(
            foot,
            text=f"Data · {BASE}   ·   grey = help · navy = labels · blue = actions",
            bg=BG, fg=MUTED, font=("Helvetica", 10),
        ).pack(anchor=tk.W)

    # ── v6.6.5: Siril-style 3-tab top panel (Stacking / Derotate / Process) ─
    def _build_top_panel(self):
        """Siril-style 3-tab top panel: Stacking / Derotate / Process.

        A compact workflow strip above the main body. Stacking and Derotate
        operate on a folder of frames chosen on the Stacking tab; Process is
        the existing single-image measurement path (open file → measure).
        """
        panel = tk.Frame(self, bg=BG)
        panel.pack(fill=tk.X, padx=14, pady=(0, 4))
        self.siril_nb = ttk.Notebook(panel)
        self.siril_nb.pack(fill=tk.X)

        # ── Stacking ─────────────────────────────────────────────────────
        tab_stack = tk.Frame(self.siril_nb, bg=PANEL)
        self.siril_nb.add(tab_stack, text="  Stacking  ")
        self.stack_folder_path = None
        self._action_btn(
            tab_stack, "Choose SER / PNG folder…", self.on_stack_pick_folder,
            "Pick a folder of Jupiter frames (PNG / JPG / FITS). One file = one frame.",
            secondary=True,
        )
        self.stack_folder_lbl = tk.Label(
            tab_stack, text="No folder chosen", bg=PANEL, fg=MUTED,
            font=("Helvetica", 11), wraplength=900, justify=tk.LEFT,
        )
        self.stack_folder_lbl.pack(anchor=tk.W, padx=14, pady=(0, 4))
        self.auto_ngrid_var = tk.BooleanVar(value=True)
        self._check(
            tab_stack,
            "auto n_grid (assumes a single Jupiter-like disk in the frame)",
            self.auto_ngrid_var,
            "Sizes the AP grid from the frame. Honest limit: assumes ONE "
            "Jupiter-like disk fills the frame. Turn off and set n_grid by hand otherwise.",
        )
        self.stack_ngrid_var = tk.StringVar(value="8")
        self._labeled_entry(
            tab_stack, "n_grid (manual — used when auto is off)", self.stack_ngrid_var,
            "AP-grid resolution, e.g. 6–12.",
        )
        # v6.7.6: planet-generalised stacker option (per-lat/flow warp, quality gate)
        self.stack_engine_var = tk.StringVar(value="Jupiter-zonal")
        self._labeled_combo(
            tab_stack, "Stack engine", self.stack_engine_var,
            ["Jupiter-zonal", "Planetary (multi-planet)"],
            "Jupiter-zonal = original v6.6.3 stacker. Planetary = v6.7 planet-generalised "
            "stacker (per-lat/flow warp, lucky-imaging gate, report card, RGB).",
        )
        self.stack_planet_var = tk.StringVar(value="Jupiter")
        self._labeled_combo(
            tab_stack, "Planet (Planetary engine)", self.stack_planet_var,
            ["Jupiter", "Saturn", "Neptune", "Uranus", "Mars"],
            "Geometry / rotation / wind profile for the Planetary engine.",
        )
        self.stack_warp_var = tk.StringVar(value="per_latitude")
        self._labeled_combo(
            tab_stack, "Warp mode (Planetary engine)", self.stack_warp_var,
            ["per_latitude", "flow", "global"],
            "per_latitude = robust default. flow = dense 2D (clean/large-motion). "
            "global = legacy single translation.",
        )
        self.stack_qgate_var = tk.StringVar(value="1.0")
        self._labeled_entry(
            tab_stack, "Quality gate 0..1 (Planetary engine)", self.stack_qgate_var,
            "Keep the sharpest fraction of frames (lucky imaging). 1.0 = keep all.",
        )
        self.stack_prog = ttk.Progressbar(
            tab_stack, orient=tk.HORIZONTAL, mode="determinate",
            length=420, maximum=100,
        )
        self.stack_prog.pack(anchor=tk.W, padx=14, pady=(2, 2))
        self.stack_prog["value"] = 0
        self._action_btn(
            tab_stack, "▶  Run Stack", self.on_stacking_run,
            "Stack the folder with the Jupiter-zonal stacker. The bar tracks "
            "frame ingest, not output quality.",
            color=ACCENT,
        )

        # ── Derotate ─────────────────────────────────────────────────────
        tab_der = tk.Frame(self.siril_nb, bg=PANEL)
        self.siril_nb.add(tab_der, text="  Derotate  ")
        tk.Label(
            tab_der, text="GRS anchor — paste a GRS x,y from any frame:",
            bg=PANEL, fg=FG, font=("Helvetica", 12, "bold"),
        ).pack(anchor=tk.W, padx=14, pady=(8, 2))
        dxy = tk.Frame(tab_der, bg=PANEL)
        dxy.pack(fill=tk.X, padx=14)
        self.grs_x_var = tk.StringVar(value="")
        self.grs_y_var = tk.StringVar(value="")
        tk.Label(dxy, text="GRS x (px):", bg=PANEL, fg=FG, font=("Helvetica", 11)).pack(side=tk.LEFT)
        tk.Entry(dxy, textvariable=self.grs_x_var, width=8, font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(4, 14))
        tk.Label(dxy, text="GRS y (px):", bg=PANEL, fg=FG, font=("Helvetica", 11)).pack(side=tk.LEFT)
        tk.Entry(dxy, textvariable=self.grs_y_var, width=8, font=("Helvetica", 11)).pack(side=tk.LEFT, padx=(4, 0))
        self.grs_anchor_var = tk.BooleanVar(value=True)
        self._check(
            tab_der, "use GRS anchor", self.grs_anchor_var,
            "Demotes APs that disagree with the GRS rotation. Needs numeric GRS "
            "x,y and a folder chosen on the Stacking tab.",
        )
        self._action_btn(
            tab_der, "▶  Run Derotate (GRS-anchor)", self.on_derotate_run,
            "Per-latitude zonal derotation of the Stacking folder. "
            "'winjupos but better' is a goal, not a measured claim.",
            color=ACCENT,
        )

        # ── Process ──────────────────────────────────────────────────────
        tab_proc = tk.Frame(self.siril_nb, bg=PANEL)
        self.siril_nb.add(tab_proc, text="  Process  ")
        self._action_btn(
            tab_proc, "Open FITS / SER / PNG…", self.on_open_file,
            "Pick your telescope stack for the main measurement.",
            secondary=True,
        )
        self._action_btn(
            tab_proc, "▶  Process full (auto + by-eye limb)", self.on_process,
            "Main action: auto measure + by-eye cyan limb + GS-ORANGE/GS-MAP publish.",
            color=ACCENT,
        )
        self._action_btn(
            tab_proc, "Resolve Ephemeris only", self.on_ephemeris,
            "CM III / distance from SPICE (+ Horizons if enabled). No GRS measure.",
            secondary=True,
        )

        # ── Video Import (APS stacking + drizzle, AutoStakkert-class) ────
        tab_vid = tk.Frame(self.siril_nb, bg=PANEL)
        self.siril_nb.add(tab_vid, text="  Video Import  ")
        self.video_path = None
        self._action_btn(
            tab_vid, "Choose SER / AVI…", self.on_video_pick,
            "Pick a planetary capture (.ser preferred; uncompressed .avi works). "
            "Frames are ranked, lucky-selected per alignment point and stacked.",
            secondary=True,
        )
        self.video_lbl = tk.Label(
            tab_vid, text="No capture chosen", bg=PANEL, fg=MUTED,
            font=("Helvetica", 11), wraplength=900, justify=tk.LEFT,
        )
        self.video_lbl.pack(anchor=tk.W, padx=14, pady=(0, 4))
        self.vid_keep_var = tk.StringVar(value="0.25")
        self._labeled_entry(
            tab_vid, "Best-frames fraction per AP (lucky imaging)", self.vid_keep_var,
            "AutoStakkert-style: for every alignment point only the sharpest "
            "fraction of frame-patches contributes. 0.25 = best 25 %.",
        )
        self.vid_drizzle_var = tk.StringVar(value="1")
        self._labeled_combo(
            tab_vid, "Drizzle factor", self.vid_drizzle_var, ["1", "2", "3"],
            "1 = off. 2 or 3 = super-resolution output grid (uses sub-pixel "
            "information between frames; measured to beat single-frame RMSE).",
        )
        self.vid_ap_var = tk.StringVar(value="32")
        self._labeled_entry(
            tab_vid, "Alignment-point size (px)", self.vid_ap_var,
            "Square AP box side. 32 px is the AutoStakkert-typical value; "
            "smaller = follows local seeing more tightly, slower.",
        )
        self.vid_quality_var = tk.StringVar(value="laplacian")
        self._labeled_combo(
            tab_vid, "Frame quality estimator", self.vid_quality_var,
            ["laplacian", "gradient", "sobel", "contrast"],
            "How each patch is scored for sharpness. laplacian is the default "
            "and the most robust to brightness gradients.",
        )
        self.vid_sharpen_var = tk.StringVar(value="none")
        self._labeled_combo(
            tab_vid, "Post-stack sharpen", self.vid_sharpen_var,
            ["none", "wavelet", "unsharp", "rl"],
            "Optional Sharpen Lab pass on the stacked image (see Sharpen Lab tab).",
        )
        self.vid_derot_var = tk.StringVar(value="none")
        self._labeled_combo(
            tab_vid, "Derotate (WinJUPOS-style rotation handling)", self.vid_derot_var,
            ["none", "prior", "hybrid", "measurement"],
            "Per-latitude rotation derotation BEFORE stacking — removes the "
            "smear a multi-minute capture builds up as the planet turns. "
            "prior = physics model only; measurement = measured cloud drifts; "
            "hybrid = measured, model-blended where the lock is weak. Needs "
            "SER per-frame stamps (real captures have them).",
        )
        self._action_btn(
            tab_vid, "▶  Run APS Stack", self.on_video_stack_run,
            "APS stack the capture: per-AP lucky selection + sub-pixel drizzle. "
            "Writes aps_stack.png / aps_weight.png / APS_REPORT.txt.",
            color=ACCENT,
        )
        self._action_btn(
            tab_vid, "▶  Video → GRS Answer (full pipeline)", self.on_video_to_answer_run,
            "The whole v6.8 production chain: read capture, APS stack, sharpen, "
            "then the published measurement path. Needs mid-exposure UTC in the "
            "time box (or SER per-frame stamps are used automatically).",
            color=ACCENT,
        )

        # ── Sharpen Lab ──────────────────────────────────────────────────
        tab_sharp = tk.Frame(self.siril_nb, bg=PANEL)
        self.siril_nb.add(tab_sharp, text="  Sharpen Lab  ")
        self.sharpen_path = None
        self._action_btn(
            tab_sharp, "Choose image…", self.on_sharpen_pick,
            "Pick a PNG / JPG stack to sharpen.", secondary=True,
        )
        self.sharpen_lbl = tk.Label(
            tab_sharp, text="No image chosen", bg=PANEL, fg=MUTED,
            font=("Helvetica", 11), wraplength=900, justify=tk.LEFT,
        )
        self.sharpen_lbl.pack(anchor=tk.W, padx=14, pady=(0, 4))
        self.sharp_method_var = tk.StringVar(value="wavelet")
        self._labeled_combo(
            tab_sharp, "Method", self.sharp_method_var,
            ["wavelet", "unsharp", "rl"],
            "wavelet = B3 à-trous band gains + MAD denoise gate (default, "
            "recommended). rl = Richardson–Lucy deconvolution. unsharp = "
            "classic sharpen. RGB is sharpened on luminance only (hue preserved).",
        )
        self.sharp_amount_var = tk.StringVar(value="1.0")
        self._labeled_entry(
            tab_sharp, "Strength (unsharp amount / wavelet gain scale)", self.sharp_amount_var,
            "1.0 = standard. The wavelet method is gain-tested not to amplify noise.",
        )
        self._action_btn(
            tab_sharp, "▶  Sharpen", self.on_sharpen_run,
            "Sharpen and write <name>_sharp_<method>.png next to the input.",
            color=ACCENT,
        )

        # ── Transits (WinJUPOS-class event planner) ──────────────────────
        tab_tr = tk.Frame(self.siril_nb, bg=PANEL)
        self.siril_nb.add(tab_tr, text="  Transits  ")
        self.tr_time_var = tk.StringVar(value="")
        self._labeled_entry(
            tab_tr, "Start UTC (blank = now)  YYYY-MM-DD HH:MM", self.tr_time_var,
            "Times are naive UTC, the convention used across the whole app.",
        )
        self.tr_days_var = tk.StringVar(value="1.0")
        self._labeled_entry(
            tab_tr, "Window (days)", self.tr_days_var,
            "How far ahead to plan. 1 day lists tonight's events.",
        )
        self.tr_moons_var = tk.StringVar(value="io,europa,ganymede,callisto")
        self._labeled_entry(
            tab_tr, "Moons (comma list, blank = GRS only)", self.tr_moons_var,
            "Galilean moon transit/occultation predictions via SPICE (jup365) "
            "or ephem fallback — validated against published 2026 event tables.",
        )
        self._action_btn(
            tab_tr, "▶  Plan night", self.on_transits_run,
            "GRS transits + visibility windows + moon events, printed to the log.",
            color=ACCENT,
        )

        # ── RGB Combine (filter-wheel derotation, v6.9) ──────────────────
        tab_rgb = tk.Frame(self.siril_nb, bg=PANEL)
        self.siril_nb.add(tab_rgb, text="  RGB Combine  ")
        self.rgb_paths = {"R": None, "G": None, "B": None}
        self.rgb_lbls = {}
        for ch_ in "RGB":
            self._action_btn(
                tab_rgb, f"Choose {ch_} stack…",
                lambda c=ch_: self.on_rgb_pick(c),
                f"Pick the {ch_}-channel mono stack (PNG/JPG). Each filter "
                "sequence is derotated to the common epoch before compositing.",
                secondary=True,
            )
            lbl = tk.Label(tab_rgb, text=f"{ch_}: no image", bg=PANEL, fg=MUTED,
                           font=("Helvetica", 11), wraplength=900, justify=tk.LEFT)
            lbl.pack(anchor=tk.W, padx=14, pady=(0, 4))
            self.rgb_lbls[ch_] = lbl
        self.rgb_offsets_var = tk.StringVar(value="-240,0,240")
        self._labeled_entry(
            tab_rgb, "Channel mid-time offsets from G (s):  R,G,B — ignored when G time set",
            self.rgb_offsets_var,
            "Rotation between filter sessions in seconds. E.g. '-240,0,240' if "
            "R was shot 4 min before G and B 4 min after. With --tg ISO time "
            "set, R/G/B times are added; else offsets from G=0 are used.",
        )
        self.rgb_tg_var = tk.StringVar(value="")
        self._labeled_entry(
            tab_rgb, "Green mid-time UTC ISO (optional)", self.rgb_tg_var,
            "Sets absolute epoch; R/B times = ISO times on the same line or offsets.",
        )
        self.rgb_sublat_var = tk.StringVar(value="0.0")
        self._labeled_entry(
            tab_rgb, "Sub-Earth latitude (deg) at session", self.rgb_sublat_var,
            "From the ephemeris (Process tab / SPICE). 0 = equator-on.",
        )
        self.rgb_pa_var = tk.StringVar(value="0.0")
        self._labeled_entry(
            tab_rgb, "North pole PA (deg E of N) at session", self.rgb_pa_var,
            "Jupiter's pole PA reaches ±17 deg over a Jovian year — with this "
            "set, derotation stays exact even when north is not up.",
        )
        self._action_btn(
            tab_rgb, "▶  Combine (rotation-derotated)", self.on_rgb_combine_run,
            "Derotate each channel to the G epoch with the exact spheroid "
            "ephemeris + band-polish, gain-match, composite RGB, and report "
            "the colour-fringe improvement.",
            color=ACCENT,
        )

        # ── Analysis (session plan / wind / GRS drift, v6.9) ─────────────
        tab_an = tk.Frame(self.siril_nb, bg=PANEL)
        self.siril_nb.add(tab_an, text="  Analysis  ")
        self._section(tab_an, "Session planner (physics budgets)")
        self.an_a_var = tk.StringVar(value="0")
        self._labeled_entry(
            tab_an, "Image scale a_eq (px per R_eq; 0 = ephemeris only)",
            self.an_a_var,
            "Your equatorial disk radius in pixels — from any stack's "
            "navigation. The smear budget tables need a real scale.",
        )
        self.an_budget_var = tk.StringVar(value="1.0")
        self._labeled_entry(
            tab_an, "Smear budget (px)", self.an_budget_var,
            "How much rotation blur you tolerate inside one stack. 1 px "
            "keeps drizzle-grade detail; 2 px is forgiving.",
        )
        self._action_btn(
            tab_an, "▶  Session plan", self.on_session_plan_run,
            "Exact smear/span budgets, filter-wheel gap limits, and tonight's "
            "GRS windows — all numbers from the same rotation model the "
            "derotator uses.",
            color=ACCENT,
        )
        self._section(tab_an, "Cloud-tracking wind analysis")
        self._action_btn(
            tab_an, "Choose stack report (wind_report JSON)…", self.on_wind_pick,
            "Any video-stack report JSON written with derotate measurement/"
            "hybrid contains the measured per-latitude wind profile.",
            secondary=True,
        )
        self.wind_lbl = tk.Label(tab_an, text="No report chosen", bg=PANEL,
                                 fg=MUTED, font=("Helvetica", 11),
                                 wraplength=900, justify=tk.LEFT)
        self.wind_lbl.pack(anchor=tk.W, padx=14, pady=(0, 4))
        self.wind_path = None
        self._action_btn(
            tab_an, "▶  Wind analysis", self.on_wind_run,
            "Offset fits (advection vs System-III angular, shape-discriminated), "
            "jet detection, CSV + PNG profile panel.",
            color=ACCENT,
        )
        self._section(tab_an, "GRS System-II drift")
        self._action_btn(
            tab_an, "Choose JUPOS CSV of GRS epochs…", self.on_drift_pick,
            "Our jupos-export or the community database format — L_II vs time.",
            secondary=True,
        )
        self.drift_lbl = tk.Label(tab_an, text="No CSV chosen", bg=PANEL,
                                  fg=MUTED, font=("Helvetica", 11),
                                  wraplength=900, justify=tk.LEFT)
        self.drift_lbl.pack(anchor=tk.W, padx=14, pady=(0, 4))
        self.drift_path = None
        self._action_btn(
            tab_an, "▶  Fit drift", self.on_drift_run,
            "Sigma-clipped drift rate (deg/30d convention), curvature F-test, "
            "implied zonal velocity in m/s, prediction cone, PNG panel.",
            color=ACCENT,
        )

    # ── Stacking / Derotate handlers ──────────────────────────────────────
    def on_stack_pick_folder(self):
        d = filedialog.askdirectory(title="Choose a folder of Jupiter frames (PNG / JPG / FITS)")
        if d:
            self.stack_folder_path = Path(d)
            self.stack_folder_lbl.configure(text=f"Folder: {d}", fg=FG)
            CONSOLE.info(f"Stacking folder: {d}")

    def _stack_frames(self):
        """Load grayscale frames from the Stacking folder; post ingest progress."""
        if not getattr(self, "stack_folder_path", None):
            raise RuntimeError("Choose a SER / PNG folder on the Stacking tab first.")
        import glob
        folder = self.stack_folder_path
        exts = ("*.png", "*.jpg", "*.jpeg", "*.fit", "*.fits", "*.fts")
        files = []
        for e in exts:
            files += glob.glob(str(folder / e))
        files = sorted(set(files), key=str.lower)
        if not files:
            raise RuntimeError(f"No PNG / JPG / FITS frames found in: {folder}")
        frames = []
        for i, f in enumerate(files):
            p = Path(f)
            suf = p.suffix.lower()
            if suf in (".png", ".jpg", ".jpeg"):
                if not _HAS_PIL:
                    raise RuntimeError("Pillow is required to read PNG / JPG frames.")
                arr = np.asarray(Image.open(p).convert("L"), dtype=np.float64)
            else:
                import grs_complete_system as grs
                arr, _ = grs.read_fits(p)
                arr = np.asarray(arr, dtype=np.float64)
                if arr.ndim == 3 and arr.shape[0] == 3:
                    arr = 0.3 * arr[0] + 0.5 * arr[1] + 0.2 * arr[2]
            frames.append(arr)
            self.msg_q.put(("progress", int(round(100 * (i + 1) / len(files)))))
        return frames

    def _resolve_n_grid(self, frames) -> int:
        if self.auto_ngrid_var.get():
            from jupiter_zonal_stacker import auto_n_grid
            h, w = frames[0].shape[:2]
            return auto_n_grid(h, w)
        try:
            return max(3, min(20, int(self.stack_ngrid_var.get())))
        except Exception:
            return 8

    def on_stacking_run(self):
        try:
            self.stack_prog["value"] = 0
        except Exception:
            pass
        engine = self.stack_engine_var.get()
        is_planetary = engine.startswith("Planetary")

        def job():
            frames = self._stack_frames()
            n_grid = self._resolve_n_grid(frames)
            run_dir = BASE / "outputs" / "stack_runs" / (
                ("planetary_" if is_planetary else "zonal_")
                + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            )
            if is_planetary:
                from planet_models import get_planet
                from planetary_stacker import run_planetary_stacker
                planet = get_planet(self.stack_planet_var.get())
                try:
                    qg = max(0.0, min(1.0, float(self.stack_qgate_var.get())))
                except Exception:
                    qg = 1.0
                res = run_planetary_stacker(
                    frames, run_dir, planet=planet, n_grid=n_grid,
                    warp_mode=self.stack_warp_var.get(), quality_gate=qg, save=True,
                )
                self.msg_q.put(("progress", 100))
                txt = (
                    f"Planetary stack done ({planet.name}).\n"
                    f"  warp mode:       {res.warp_mode}\n"
                    f"  grid:            {res.n_grid}x{res.n_grid} ({res.n_aps} APs)\n"
                    f"  reference frame: #{res.reference_index}\n"
                    f"  quality gate:    {res.quality_gate:.2f} "
                    f"(dropped {len(res.dropped_frames)}: {res.dropped_frames or 'none'})\n"
                    f"  mean drift RMS:  {res.mean_rms_drift_px:.3f} px\n"
                    f"  consistency:     {res.warp_consistency_std:.4f}\n"
                    f"  elapsed:         {res.elapsed_s:.1f} s\n"
                    f"  stacked PNG:     {res.output_path}\n"
                    f"  report card:     {run_dir / 'stacker_report.txt'}\n"
                )
                for n in res.notes:
                    txt += f"  - {n}\n"
                return {"text": txt, "preview": res.output_path}

            from jupiter_zonal_stacker import run_jupiter_zonal_stacker
            res = run_jupiter_zonal_stacker(frames, run_dir, n_grid=n_grid, save=True)
            self.msg_q.put(("progress", 100))
            txt = (
                "Jupiter-zonal stack done.\n"
                f"  frames:         {res.n_frames}\n"
                f"  grid (n_grid):  {res.n_grid}x{res.n_grid}\n"
                f"  GRS anchor:     {'yes' if res.grs_anchor_used else 'no'}\n"
                f"  mean drift RMS: {res.mean_rms_drift_px:.3f} px\n"
                f"  elapsed:        {res.elapsed_s:.1f} s\n"
                f"  stacked PNG:    {res.output_path}\n"
            )
            for n in res.notes:
                txt += f"  - {n}\n"
            return {"text": txt, "preview": res.output_path}

        self._run_bg("Stack (Planetary)" if is_planetary else "Stack (Jupiter-zonal)", job)

    def on_derotate_run(self):
        try:
            self.stack_prog["value"] = 0
        except Exception:
            pass

        def job():
            frames = self._stack_frames()
            from jupiter_zonal_derotator import run_jupiter_zonal_derotate
            out = BASE / "outputs" / "derotate_runs" / (
                "zonal_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            )
            out.mkdir(parents=True, exist_ok=True)
            dres = run_jupiter_zonal_derotate(frames, out, mode="measurement", save=True)
            txt = (
                "Zonal derotate done.\n"
                f"  frames:             {dres.n_frames}\n"
                f"  mean per-row shift: {dres.mean_per_row_shift_px:.3f} px\n"
                f"  elapsed:            {dres.elapsed_s:.1f} s\n"
                f"  stacked PNG:        {dres.output_path}\n"
            )
            prev = dres.output_path
            if self.grs_anchor_var.get():
                try:
                    grs_xy = (float(self.grs_x_var.get()), float(self.grs_y_var.get()))
                except (ValueError, TypeError):
                    grs_xy = None
                if grs_xy is not None:
                    gx, gy = grs_xy
                    from jupiter_zonal_stacker import run_jupiter_zonal_stacker
                    sres = run_jupiter_zonal_stacker(
                        frames, out / "grs_anchor",
                        n_grid=self._resolve_n_grid(frames), grs_xy=grs_xy, save=True,
                    )
                    txt += (
                        f"  GRS-anchor stack (x={gx}, y={gy}):\n"
                        f"    anchor used: {sres.grs_anchor_used}\n"
                        f"    grid:        {sres.n_grid}x{sres.n_grid}\n"
                        f"    drift RMS:   {sres.mean_rms_drift_px:.3f} px\n"
                    )
                    for n in sres.notes:
                        txt += f"    - {n}\n"
                else:
                    txt += "  GRS anchor: skipped (enter numeric GRS x and y to enable)\n"
            self.msg_q.put(("progress", 100))
            return {"text": txt, "preview": prev}

        self._run_bg("Derotate (zonal, GRS-anchor)", job)

    # ── Video Import / Sharpen Lab / Transits handlers (v6.8) ────────────
    def on_video_pick(self):
        f = filedialog.askopenfilename(
            title="Choose a planetary capture",
            filetypes=[("Planetary video", "*.ser *.avi"), ("All files", "*.*")],
        )
        if f:
            self.video_path = Path(f)
            self.video_lbl.configure(text=f"Capture: {f}", fg=FG)
            CONSOLE.info(f"Video capture: {f}")

    def _vid_cfg(self):
        try:
            keep = max(0.05, min(1.0, float(self.vid_keep_var.get())))
        except Exception:
            keep = 0.25
        try:
            drizzle = int(self.vid_drizzle_var.get())
            drizzle = drizzle if drizzle in (1, 2, 3) else 1
        except Exception:
            drizzle = 1
        try:
            ap = max(8, min(256, int(self.vid_ap_var.get())))
        except Exception:
            ap = 32
        derot = str(getattr(self, "vid_derot_var", tk.StringVar(value="none")).get() or "none")
        if derot not in ("none", "prior", "hybrid", "measurement"):
            derot = "none"
        return (keep, drizzle, ap, str(self.vid_quality_var.get()),
                str(self.vid_sharpen_var.get()), derot)

    def on_video_stack_run(self):
        if not getattr(self, "video_path", None):
            messagebox.showinfo("Video Import", "Choose a SER / AVI capture first.")
            return

        def job():
            import observatory_pipeline as op
            keep, drizzle, ap, quality, sharp, derot = self._vid_cfg()
            out = BASE / "outputs" / "aps_runs" / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            rep = op.stack_video(
                str(self.video_path), out_dir=out, keep_frac=keep, drizzle=drizzle,
                ap_size=ap, quality=quality, sharpen_method=sharp, derotate=derot,
            )
            self.msg_q.put(("progress", 100))
            dinfo = rep.get("derotate") or {}
            dtxt = (f"  derotate:        {dinfo.get('mode')} "
                    f"(ref frame #{dinfo.get('ref_index')}, "
                    f"median row shift {dinfo.get('median_per_row_shift_px', 0.0):.2f} px)\n"
                    if dinfo else "")
            txt = (
                "APS stack done (AutoStakkert-class).\n"
                f"  frames:          {rep.get('n_frames')}\n"
                f"  APs:             {rep.get('n_aps')}\n"
                f"  drizzle:         x{rep.get('drizzle')}\n"
                + dtxt +
                f"  local shift RMS: {rep.get('mean_local_shift_rms', 0.0):.3f} px\n"
                f"  elapsed:         {rep.get('secs', 0.0):.1f} s\n"
                f"  stack PNG:       {rep.get('stack_png')}\n"
                f"  weight PNG:      {out / 'aps_weight.png'}\n"
                f"  full report:     {out / 'APS_REPORT.txt'}\n"
            )
            return {"text": txt, "preview": rep.get("stack_png")}

        self._run_bg("APS Stack (Video Import)", job)

    def on_video_to_answer_run(self):
        if not getattr(self, "video_path", None):
            messagebox.showinfo("Video Import", "Choose a SER / AVI capture first.")
            return

        def job():
            import observatory_pipeline as op
            keep, drizzle, ap, quality, _sharp, derot = self._vid_cfg()
            t = (self.time_var.get() or "").strip() or None
            rep = op.video_to_answer(
                str(self.video_path), time_utc=t, keep_frac=keep, drizzle=drizzle,
                ap_size=ap, sharpen_method="wavelet", derotate=derot,
            )
            self.msg_q.put(("progress", 100))
            dinfo = rep.get("derotate") or {}
            txt = (
                "Video → GRS answer done.\n"
                f"  frames used: {rep.get('n_frames_used')} / {rep.get('n_frames_video')}\n"
                f"  epoch:       {rep.get('time_utc')}  [{rep.get('measurement_epoch', 'mid_exposure')}]\n"
                + (f"  derotate:    {dinfo.get('mode')} anchored to ref frame "
                   f"#{dinfo.get('ref_index')} ({dinfo.get('ref_time_utc')})\n"
                   if dinfo else "")
                + f"  stack:       {rep.get('stack_png')}\n"
            )
            meas = rep.get("measurement")
            if meas:
                h = meas.get("headline") or {}
                pub = meas.get("publish") or {}
                lon = pub.get("publish_lon_iii_deg", h.get("lon_iii_deg"))
                lat = pub.get("publish_lat_deg", h.get("lat_deg"))
                txt += (
                    f"  GRS lon III: {lon}\n"
                    f"  GRS lat:     {lat}\n"
                    f"  definition:  {pub.get('publish_definition')}\n"
                    f"  grade:       {h.get('grade')}\n"
                )
            else:
                txt += f"  note: {rep.get('note')}\n"
            return {"text": txt, "preview": rep.get("stack_png")}

        self._run_bg("Video → GRS Answer", job)

    def on_sharpen_pick(self):
        f = filedialog.askopenfilename(
            title="Choose an image to sharpen",
            filetypes=[("Images", "*.png *.jpg *.jpeg"), ("All files", "*.*")],
        )
        if f:
            self.sharpen_path = Path(f)
            self.sharpen_lbl.configure(text=f"Image: {f}", fg=FG)

    def on_sharpen_run(self):
        if not getattr(self, "sharpen_path", None):
            messagebox.showinfo("Sharpen Lab", "Choose an image first.")
            return

        def job():
            import observatory_pipeline as op
            try:
                amt = max(0.1, min(4.0, float(self.sharp_amount_var.get())))
            except Exception:
                amt = 1.0
            rep = op.sharpen_file(
                str(self.sharpen_path), method=str(self.sharp_method_var.get()),
                amount=amt,
            )
            self.msg_q.put(("progress", 100))
            txt = (
                f"Sharpen Lab ({rep.get('method')}).\n"
                f"  Laplacian variance: {rep.get('lapvar_before', 0.0):.4f} → "
                f"{rep.get('lapvar_after', 0.0):.4f} "
                f"(x{rep.get('lapvar_after', 0.0) / max(1e-12, rep.get('lapvar_before', 1.0)):.2f})\n"
                f"  output: {rep.get('out')}\n"
            )
            return {"text": txt, "preview": rep.get("out")}

        self._run_bg("Sharpen Lab", job)

    def on_transits_run(self):
        def job():
            import transits
            t = (self.tr_time_var.get() or "").strip() or None
            try:
                days = max(0.25, min(30.0, float(self.tr_days_var.get())))
            except Exception:
                days = 1.0
            moons = tuple(m.strip() for m in (self.tr_moons_var.get() or "").split(",")
                          if m.strip())
            t0 = transits._to_utc(t) if t else datetime.now(timezone.utc)
            plan = transits.night_planner(t0, days=days, moons=moons)
            self.msg_q.put(("progress", 100))
            return {"text": transits.planner_text(plan)}

        self._run_bg("Transit planner", job)

    # ── v6.9 handlers: RGB Combine + Analysis panels ─────────────────────
    def on_rgb_pick(self, ch):
        f = filedialog.askopenfilename(
            title=f"Choose {ch}-channel mono stack",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff")])
        if f:
            self.rgb_paths[ch] = Path(f)
            self.rgb_lbls[ch].configure(text=f"{ch}: {f}", fg=FG)

    def on_rgb_combine_run(self):
        if not all(self.rgb_paths.get(c) for c in "RGB"):
            messagebox.showinfo("RGB Combine", "Choose R, G and B stacks first.")
            return

        def job():
            import rgb_combine as _rc
            from planet_models import JUPITER
            from precision_engine import fit_limb_nav, to_mono
            from observatory_pipeline import _save_png
            import numpy as _np

            def _load(p):
                im = _np.asarray(Image.open(p))
                if im.dtype == _np.uint8:
                    im = im.astype(_np.float64) / 255.0
                return to_mono(im.astype(_np.float64))

            g_img = _load(self.rgb_paths["G"])
            r_img = _load(self.rgb_paths["R"])
            b_img = _load(self.rgb_paths["B"])
            offs = [float(x) for x in self.rgb_offsets_var.get().split(",")]
            while len(offs) < 3:
                offs.append(0.0)
            tg_s = (self.rgb_tg_var.get() or "").strip()
            if tg_s:
                t_ref = datetime.fromisoformat(tg_s).timestamp()
                tr, tb = t_ref + offs[0] - offs[1], t_ref + offs[2] - offs[1]
            else:
                t_ref, tr, tb = 0.0, offs[0] - offs[1], offs[2] - offs[1]
            sub_lat = float(self.rgb_sublat_var.get() or 0.0)
            pa = float(self.rgb_pa_var.get() or 0.0)
            nav = fit_limb_nav(g_img, cm_iii_deg=0.0,
                               distance_au=JUPITER.default_distance_au,
                               north_pa_deg=pa)
            nav.flattening = JUPITER.flattening
            nav.sub_lat_deg = sub_lat
            nav.north_pa_deg = pa
            res = _rc.combine_rgb(r_img, g_img, b_img, tr, t_ref, tb,
                                  JUPITER, nav, t_ref_s=t_ref)
            out_dir = Path(self.rgb_paths["G"]).parent
            rgb_path = _save_png(out_dir / "rgb_combined.png", res.rgb)
            (out_dir / "rgb_report.json").write_text(
                json.dumps(res.report, indent=2, default=str), encoding="utf-8")
            self.msg_q.put(("progress", 100))
            return {"text": _rc.combine_report_text(res) +
                    f"\n\nrgb: {rgb_path}", "preview": str(rgb_path)}

        self._run_bg("RGB Combine", job)

    def on_session_plan_run(self):
        def job():
            from planet_models import JUPITER
            from session_planner import session_plan, plan_text
            try:
                a_eq = float(self.an_a_var.get() or 0.0)
            except Exception:
                a_eq = 0.0
            try:
                budget = float(self.an_budget_var.get() or 1.0)
            except Exception:
                budget = 1.0
            plan = session_plan(datetime.now(timezone.utc).replace(tzinfo=None),
                                8.0, planet=JUPITER, a_eq_px=a_eq,
                                budget_px=budget)
            self.msg_q.put(("progress", 100))
            return {"text": plan_text(plan)}

        self._run_bg("Session plan", job)

    def on_wind_pick(self):
        f = filedialog.askopenfilename(
            title="Choose a video-stack report JSON",
            filetypes=[("JSON", "*.json")])
        if f:
            self.wind_path = Path(f)
            self.wind_lbl.configure(text=f"Report: {f}", fg=FG)

    def on_wind_run(self):
        if not self.wind_path:
            messagebox.showinfo("Wind analysis", "Choose a stack report JSON first.")
            return

        def job():
            from planet_models import JUPITER
            from wind_analysis import (wind_report_text, render_profile_png,
                                       export_profile_csv, detect_jets,
                                       summarize_profile)
            rep = json.loads(Path(self.wind_path).read_text(encoding="utf-8"))
            wr = rep.get("wind_report")
            if not wr:
                raise RuntimeError("report has no wind_report block — stack "
                                   "with derotate measurement/hybrid first")
            out_dir = Path(self.wind_path).parent
            png = render_profile_png(
                wr, str(out_dir / "wind_profile.png"), jets=detect_jets(wr))
            csv_p = export_profile_csv(
                wr, str(out_dir / "wind_profile.csv"),
                summary=summarize_profile(JUPITER, wr))
            self.msg_q.put(("progress", 100))
            return {"text": wind_report_text(JUPITER, wr) +
                    f"\n\npng: {png}\ncsv: {csv_p}", "preview": png}

        self._run_bg("Wind analysis", job)

    def on_drift_pick(self):
        f = filedialog.askopenfilename(
            title="Choose a JUPOS CSV of GRS epochs",
            filetypes=[("CSV", "*.csv")])
        if f:
            self.drift_path = Path(f)
            self.drift_lbl.configure(text=f"CSV: {f}", fg=FG)

    def on_drift_run(self):
        if not self.drift_path:
            messagebox.showinfo("GRS drift", "Choose a JUPOS CSV first.")
            return

        def job():
            from planet_models import JUPITER
            from grs_drift import (points_from_jupos_csv, fit_drift,
                                   drift_report_text, render_drift_png,
                                   export_drift_csv)
            pts = points_from_jupos_csv(self.drift_path)
            if len(pts) < 3:
                raise RuntimeError(f"only {len(pts)} usable GRS epochs")
            fit = fit_drift(pts, lat_ref_deg=-20.0)
            out_dir = Path(self.drift_path).parent
            png = render_drift_png(pts, fit, str(out_dir / "grs_drift.png"))
            csv_p = export_drift_csv(pts, fit, str(out_dir / "grs_drift_fit.csv"))
            self.msg_q.put(("progress", 100))
            return {"text": drift_report_text(fit, planet=JUPITER) +
                    f"\n\npng: {png}\ncsv: {csv_p}", "preview": png}

        self._run_bg("GRS drift", job)

    def _section(self, parent, title: str):
        """Section header: bold navy text on tinted strip with accent underline."""
        wrap = tk.Frame(parent, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
        wrap.pack(fill=tk.X, padx=12, pady=(18, 6))
        tk.Label(
            wrap, text=title.upper(), bg=PANEL2, fg=FG,
            font=("Helvetica", 11, "bold"),
        ).pack(anchor=tk.W, padx=12, pady=(8, 2))
        # Thin accent underline
        accent_bar = tk.Frame(wrap, bg=ACCENT, height=2)
        accent_bar.pack(fill=tk.X, padx=12, pady=(0, 6))

    def _labeled_entry(self, parent, label: str, var: tk.StringVar, desc: str = ""):
        tk.Label(
            parent, text=label, bg=PANEL, fg=FG,
            font=("Helvetica", 12, "bold"),
        ).pack(anchor=tk.W, padx=14, pady=(6, 0))
        if desc:
            tk.Label(
                parent, text=desc, bg=PANEL, fg=MUTED,
                font=("Helvetica", 11), wraplength=340, justify=tk.LEFT,
            ).pack(anchor=tk.W, padx=14, pady=(1, 3))
        e = tk.Entry(
            parent, textvariable=var,
            bg=INPUT_BG, fg=FG, insertbackground=ACCENT,
            relief=tk.FLAT, font=("Menlo", 12),
            highlightbackground=BORDER, highlightthickness=2,
            highlightcolor=ACCENT,
        )
        e.pack(fill=tk.X, padx=14, pady=(0, 6), ipady=8)

    def _labeled_combo(self, parent, label: str, var: tk.StringVar, values, desc: str = ""):
        tk.Label(
            parent, text=label, bg=PANEL, fg=FG,
            font=("Helvetica", 12, "bold"),
        ).pack(anchor=tk.W, padx=14, pady=(6, 0))
        if desc:
            tk.Label(
                parent, text=desc, bg=PANEL, fg=MUTED,
                font=("Helvetica", 11), wraplength=340, justify=tk.LEFT,
            ).pack(anchor=tk.W, padx=14, pady=(1, 3))
        cb = ttk.Combobox(
            parent, textvariable=var, values=values,
            state="readonly", font=("Helvetica", 12),
        )
        cb.pack(fill=tk.X, padx=14, pady=(0, 6), ipady=4)

    def _check(self, parent, text: str, var: tk.BooleanVar, desc: str = ""):
        """Checkbox with black text + grey description under it."""
        wrap = tk.Frame(parent, bg=PANEL)
        wrap.pack(fill=tk.X, padx=12, pady=4)
        c = tk.Checkbutton(
            wrap,
            text=text,
            variable=var,
            bg=PANEL,
            fg=FG,
            activebackground=PANEL,
            activeforeground=FG,
            selectcolor="#e5e7eb",
            font=("Helvetica", 12, "bold"),
            anchor=tk.W,
            cursor="hand2",
            highlightthickness=0,
        )
        c.pack(anchor=tk.W, fill=tk.X)
        if desc:
            tk.Label(
                wrap, text=desc, bg=PANEL, fg=MUTED,
                font=("Helvetica", 11), wraplength=340, justify=tk.LEFT,
            ).pack(anchor=tk.W, padx=(22, 0), pady=(0, 2))

    def _action_btn(
        self,
        parent,
        text: str,
        cmd,
        desc: str = "",
        color: str = ACCENT,
        secondary: bool = False,
    ):
        """Full-width button with grey description underneath (no ? icons)."""
        wrap = tk.Frame(parent, bg=PANEL)
        wrap.pack(fill=tk.X, padx=12, pady=(8, 3))
        if secondary:
            b = tk.Button(
                wrap, text=text, command=cmd,
                bg=PANEL2, fg=FG,
                activebackground="#dde1e8", activeforeground=FG,
                relief=tk.FLAT, font=("Helvetica", 12, "bold"),
                padx=14, pady=10, cursor="hand2",
                highlightbackground=BORDER, highlightthickness=1,
            )
        else:
            b = tk.Button(
                wrap, text=text, command=cmd,
                bg=color, fg=BTN_TEXT,
                activebackground=ACCENT_HOVER, activeforeground=BTN_TEXT,
                relief=tk.FLAT, font=("Helvetica", 13, "bold"),
                padx=14, pady=13, cursor="hand2",
                highlightbackground=BORDER, highlightthickness=1,
            )
        b.pack(fill=tk.X)
        if desc:
            tk.Label(
                wrap, text=desc, bg=PANEL, fg=MUTED,
                font=("Helvetica", 11), wraplength=340, justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(4, 2))
        return b

    # ── helpers ────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool, status: str = ""):
        self.busy = busy
        if busy:
            self.status_var.set("● " + (status or "RUNNING…"))
            self.status_lbl.configure(fg=WARN)
            try:
                self.prog.start(12)
            except Exception:
                pass
        else:
            self.status_var.set("● " + (status or "IDLE"))
            self.status_lbl.configure(fg=OK if status == "DONE" else (ERR if status == "ERROR" else SHADOW_BG))
            try:
                self.prog.stop()
            except Exception:
                pass

    def _log_ui(self, level: str, msg: str):
        tag = level if level in ("OK", "WARN", "ERROR", "INFO", "DEBUG") else "INFO"
        self.console.insert(tk.END, f"[{level}] {msg}\n", tag)
        self.console.see(tk.END)

    def _results(self, text: str):
        """Fill Full Results; Dashboard is updated in _update_metrics."""
        self.results.delete("1.0", tk.END)
        self.results.insert(tk.END, text)
        # Show Dashboard (small table) first after a run
        try:
            self.nb.select(1)
        except Exception:
            pass

    def _update_metrics(self, package: Dict[str, Any]):
        if not package.get("publish"):
            try:
                from publish_primary import apply_publish_policy
                apply_publish_policy(package)
            except Exception:
                pass
        h = package.get("headline") or {}
        pub = package.get("publish") or {}
        eq = (pub.get("winjupos_equality") or {})
        # Prefer champion / superduper grade over WinJUPOS equality label
        grade = (
            h.get("champion_grade")
            or h.get("superduper_grade")
            or (package.get("champion") or {}).get("grade")
            or eq.get("agreement")
            or h.get("winjupos_agreement")
            or h.get("grade")
            or h.get("calibration_grade")
            or "—"
        )
        self.metric_vars["grade"].set(str(grade)[:28])
        lon = pub.get("publish_lon_iii_deg", h.get("publish_lon_iii_deg", h.get("lon_iii_deg")))
        lat = pub.get("publish_lat_deg", h.get("publish_lat_deg", h.get("lat_deg")))
        self.metric_vars["lon"].set(f"{lon:.4f}°" if isinstance(lon, (int, float)) else "—")
        self.metric_vars["lat"].set(f"{lat:.4f}°" if isinstance(lat, (int, float)) else "—")
        sig = (
            pub.get("publish_sigma_sky_arcsec")
            or h.get("champion_sigma_sky_arcsec")
            or h.get("sigma_total_sky_arcsec")
        )
        # Prefer limb/definition systematics if available
        limb = pub.get("limb_outline_sky_spread_arcsec") or h.get("limb_outline_sky_spread_arcsec")
        if isinstance(limb, (int, float)) and isinstance(sig, (int, float)):
            self.metric_vars["sigma"].set(f"{max(sig, limb):.4f}")
        else:
            self.metric_vars["sigma"].set(f"{sig:.4f}" if isinstance(sig, (int, float)) else "—")
        tr = h.get("truth_recovery_sky_arcsec")
        if tr is None and package.get("truth_recovery"):
            tr = package["truth_recovery"].get("sky_error_arcsec")
        # Show vs WinJUPOS sky if present
        wj_sky = eq.get("sky_error_arcsec")
        if isinstance(wj_sky, (int, float)):
            self.metric_vars["truth"].set(f"WJ {wj_sky:.3f}")
        else:
            self.metric_vars["truth"].set(f"{tr:.4f}" if isinstance(tr, (int, float)) else "—")
        ep = h.get("synth_epoch") or h.get("user_time") or "—"
        self.metric_vars["epoch"].set(str(ep)[:19])

        # Dashboard = small table only
        try:
            from result_report import format_dashboard_table, format_human_report
            self.dash.delete("1.0", tk.END)
            self.dash.insert(tk.END, format_dashboard_table(package))
            # Full Results = everything
            full = package.get("text") or format_human_report(package)
            self.results.delete("1.0", tk.END)
            self.results.insert(tk.END, full)
        except Exception as e:
            self.dash.delete("1.0", tk.END)
            self.dash.insert(tk.END, f"Dashboard error: {e}\n")

    def _show_preview(self, path: Optional[Path]):
        if not path or not Path(path).exists():
            return
        path = Path(path)
        if not _HAS_PIL:
            self.preview_lbl.configure(text=f"{path.name}\n(Pillow required for preview)", image="")
            return
        try:
            # FITS/SER: convert to sharp PNG first (browsers/PIL cannot open FITS natively)
            show_path = path
            if path.suffix.lower() in (".fit", ".fits", ".fts", ".ser"):
                try:
                    from desktop_pipeline import write_image_preview
                    tmp = path.parent / f"_preview_{path.stem}.png"
                    write_image_preview(path, tmp, max_side=1600)
                    show_path = tmp
                except Exception as conv_e:
                    self.preview_lbl.configure(text=f"FITS preview convert failed: {conv_e}", image="")
                    return
            im = Image.open(show_path)
            self.preview_lbl.update_idletasks()
            w = max(self.preview_lbl.winfo_width(), 480)
            h = max(self.preview_lbl.winfo_height(), 320)
            im = im.copy()
            im.thumbnail((w - 16, h - 16), Image.Resampling.LANCZOS)
            self._photo = ImageTk.PhotoImage(im)
            self.preview_lbl.configure(image=self._photo, text="")
            self.nb.select(0)
        except Exception as e:
            self.preview_lbl.configure(text=f"Preview error: {e}", image="")

    def _tick(self):
        self.log_bridge.poll()
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self._log_ui(payload.get("level", "INFO"), payload.get("msg", ""))
                elif kind == "done":
                    if payload.get("error"):
                        self._set_busy(False, "ERROR")
                        self._results("Error:\n" + str(payload["error"]))
                        messagebox.showerror("Jupiter Great Red Spot Detector", str(payload["error"]))
                    else:
                        self._set_busy(False, "DONE")
                        pkg = payload.get("package") or payload.get("result") or {}
                        self.last_package = pkg if isinstance(pkg, dict) else None
                        text = payload.get("text") or ""
                        if not text and isinstance(pkg, dict):
                            from desktop_pipeline import format_full_report
                            text = format_full_report(pkg)
                        self._results(text)
                        if isinstance(pkg, dict):
                            self._update_metrics(pkg)
                        prev = payload.get("preview")
                        if prev:
                            self._show_preview(Path(prev))
                elif kind == "status":
                    self.status_var.set("● " + str(payload))
                elif kind == "progress":
                    # Determinate bar on the Stacking tab (frame-ingest %)
                    if hasattr(self, "stack_prog"):
                        try:
                            self.stack_prog["value"] = max(0, min(100, int(payload)))
                        except Exception:
                            pass
                elif kind == "nn_report":
                    init_l = payload.get("initial_loss")
                    fin_l = payload.get("final_loss")
                    gain = payload.get("improvement")
                    gp = payload.get("improvement_pct")
                    if hasattr(self, "nn_gain_lbl"):
                        self.nn_gain_lbl.configure(
                            text=(
                                f"Loss report: start={init_l} → end={fin_l}  "
                                f"gain={gain} ({gp}%)"
                            ),
                            fg=OK if (isinstance(gain, (int, float)) and gain >= 0) else ERR,
                        )
                    if hasattr(self, "nn_lbl"):
                        self.nn_lbl.configure(
                            text=f"NN: ready · {payload.get('summary') or 'trained'}",
                            fg=OK,
                        )
        except queue.Empty:
            pass
        self.after(180, self._tick)

    def _gate(self, feature: str, *, resolution: Optional[str] = None) -> bool:
        """
        Feature gate via license_manager.

        Free local default: EVAL plan (process/synth up to 4K). Set
        GRS_LICENSE_OPEN=1 to force fully open (all features, no license).
        """
        import os
        if os.environ.get("GRS_LICENSE_OPEN", "").strip() in ("1", "true", "yes"):
            return True
        try:
            from license_manager import require_feature
            from paths import data_dir
            ok, msg = require_feature(data_dir(), feature, resolution=resolution)
            if not ok:
                try:
                    messagebox.showwarning("License", msg)
                except Exception:
                    pass
                return False
            return True
        except Exception as e:
            # Fail open only for EVAL-safe features when license module broken
            if feature in ("process", "synthetic", "cli"):
                return True
            try:
                messagebox.showwarning("License", f"License check failed: {e}")
            except Exception:
                pass
            return False

    def _run_bg(self, name: str, fn):
        if self.busy:
            messagebox.showinfo("Busy", "A job is already running.")
            return
        def wrap():
            self.msg_q.put(("status", name))
            try:
                import group_access
                group_access.log_event("job_start", {"job": name})
            except Exception:
                pass
            try:
                result = fn()
                try:
                    import group_access
                    detail = {"job": name, "ok": True}
                    if isinstance(result, dict):
                        h = result.get("headline") or {}
                        detail["output_dir"] = result.get("output_dir") or h.get("output_dir")
                        if "lon_iii_deg" in h:
                            detail["lon_iii_deg"] = h.get("lon_iii_deg")
                        if "truth_recovery_sky_arcsec" in h:
                            detail["truth_recovery_sky_arcsec"] = h.get("truth_recovery_sky_arcsec")
                    group_access.log_event("job_done", detail)
                except Exception:
                    pass
                if isinstance(result, dict):
                    self.msg_q.put(("done", {
                        "text": result.get("text"),
                        "package": result,
                        "preview": result.get("preview"),
                    }))
                else:
                    self.msg_q.put(("done", {"text": str(result)}))
            except Exception as e:
                try:
                    import group_access
                    group_access.log_event("job_error", {"job": name, "error": str(e)})
                except Exception:
                    pass
                CONSOLE.error(str(e))
                CONSOLE.debug(traceback.format_exc())
                self.msg_q.put(("done", {"error": str(e)}))

        self._set_busy(True, name)
        self.worker = threading.Thread(target=wrap, daemon=True)
        self.worker.start()

    def _mc(self) -> int:
        try:
            return max(0, min(120, int(self.mc_var.get())))
        except Exception:
            return 60

    def _inj(self) -> int:
        try:
            return max(8, min(64, int(self.inj_var.get())))
        except Exception:
            return 28

    def _float_opt(self, var: tk.StringVar) -> Optional[float]:
        s = (var.get() or "").strip()
        if not s:
            return None
        try:
            return float(s)
        except Exception:
            return None

    def _aperture(self) -> float:
        try:
            return float(self.aperture_var.get() or 0.35)
        except Exception:
            return 0.35

    def _time_error(self) -> float:
        try:
            return float(self.err_var.get() or 0)
        except Exception:
            return 0.0

    # ── actions ────────────────────────────────────────────────────────
    def on_clear(self):
        self.console.delete("1.0", tk.END)
        try:
            CONSOLE.clear()
        except Exception:
            pass

    def on_open_outputs(self):
        out = BASE / "outputs"
        out.mkdir(exist_ok=True)
        os.system(f'open "{out}"')

    def on_save_results(self):
        if not self.last_package:
            messagebox.showinfo("Nothing to save", "Run a job first.")
            return
        p = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"grs_result_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json",
        )
        if p:
            Path(p).write_text(json.dumps(self.last_package, indent=2, default=str), encoding="utf-8")
            messagebox.showinfo("Saved", p)

    def on_open_file(self):
        p = filedialog.askopenfilename(
            title="Open FITS / SER / PNG",
            filetypes=[
                ("Astronomy / images", "*.fit *.fits *.fts *.ser *.png *.jpg *.jpeg"),
                ("All", "*.*"),
            ],
        )
        if p:
            self.file_path = Path(p)
            self.file_lbl.configure(text=self.file_path.name, fg=FG)
            if self.file_path.suffix.lower() in (".png", ".jpg", ".jpeg"):
                self._show_preview(self.file_path)

    def on_winjupos(self):
        p = filedialog.askopenfilename(
            title="WinJUPOS CML CSV/JSON",
            filetypes=[("Tables", "*.csv *.json *.txt"), ("All", "*.*")],
        )
        if not p:
            return
        dest = BASE / "ephemeris_data" / ("winjupos_cm.json" if p.lower().endswith(".json") else "winjupos_cm.csv")
        dest.write_bytes(Path(p).read_bytes())
        self.winjupos_path = str(dest)
        self.wj_lbl.configure(text=f"WinJUPOS: {Path(p).name}", fg=OK)
        CONSOLE.ok(f"WinJUPOS CML → {dest}")

    def _prompt_human_choice(self, *, force: bool = False, image_path=None, title=None):
        """
        AUTO limb (green) + BY EYE limb (cyan).
        force=True: always open dialog (Process full). force=False: respect dual_var (Synthetic).
        """
        from human_choice import prompt_human_choice_dialog, HumanChoice
        if not force and not self.dual_var.get():
            return HumanChoice(enabled=False)
        img = image_path
        if img is None and self.file_path and self.file_path.exists():
            img = self.file_path
        ch = prompt_human_choice_dialog(
            self,
            title=title or "Process: AUTO limb (green) + BY EYE (cyan)",
            preset=HumanChoice(
                definition="GS-MAP+RIM",
                manual_lon=self._float_opt(self.wj_lon_var),
                manual_lat=self._float_opt(self.wj_lat_var),
                use_as_publish=True,
            ),
            wj_lon=self._float_opt(self.wj_lon_var),
            wj_lat=self._float_opt(self.wj_lat_var),
            image_path=img,
        )
        return ch

    def on_synthetic(self):
        res = self.res_var.get() or "4K"
        if not self._gate("synthetic", resolution=res):
            return
        # For by-eye limb: build a quick image-only synth first so green/cyan outlines have a planet
        preview_path = None
        if self.dual_var.get() and self.synth_process_var.get():
            try:
                self.status_var.set("● Building synth preview for limb outline…")
                self.update_idletasks()
                from product_core import generate_synthetic
                prev = generate_synthetic(
                    out_root=BASE / "outputs" / "_limb_preview",
                    resolution="1080p" if res in ("4K", "8K", "16K") else res,
                    mode="metrology",
                    process_after=False,
                    use_nn=False,
                )
                preview_path = Path(prev.get("png") or "")
                if preview_path.exists():
                    self._show_preview(preview_path)
            except Exception as e:
                CONSOLE.warn(f"Synth limb preview: {e}")

        if not self.dual_var.get():
            from human_choice import HumanChoice
            choice = HumanChoice(enabled=False)
        else:
            from human_choice import prompt_human_choice_dialog, HumanChoice
            choice = prompt_human_choice_dialog(
                self,
                title="Limb: AUTO (green) + BY EYE (cyan) — Synthetic",
                preset=HumanChoice(
                    definition="GS-MAP+RIM",
                    manual_lon=self._float_opt(self.wj_lon_var),
                    manual_lat=self._float_opt(self.wj_lat_var),
                ),
                wj_lon=self._float_opt(self.wj_lon_var),
                wj_lat=self._float_opt(self.wj_lat_var),
                image_path=preview_path if preview_path and preview_path.exists() else None,
            )
        if choice is None:
            return
        hc = choice.to_dict()

        def job():
            from desktop_pipeline import run_synthetic_full
            return run_synthetic_full(
                BASE / "outputs",
                region=self.region_var.get(),
                resolution=res,
                mc_iter=self._mc(),
                injection_trials=self._inj(),
                factory_mode=self.factory_var.get(),
                use_vlbi=self.vlbi_var.get(),
                use_nn=self.nn_var.get(),
                nasa=self.nasa_var.get(),
                aperture_m=self._aperture(),
                process_after=self.synth_process_var.get(),
                mode="metrology",
                human_choice=hc,
            )
        self._run_bg("SYNTHETIC DUAL" if choice.enabled else "SYNTHETIC", job)

    def on_synthetic_only(self):
        """Generate image only — no metrology (clear separate button)."""
        def job():
            from desktop_pipeline import run_synthetic_full
            return run_synthetic_full(
                BASE / "outputs",
                region=self.region_var.get(),
                resolution=self.res_var.get() or "4K",
                mc_iter=self._mc(),
                injection_trials=self._inj(),
                factory_mode=False,
                use_vlbi=self.vlbi_var.get(),
                use_nn=False,
                nasa=False,
                aperture_m=self._aperture(),
                process_after=False,
                mode="metrology",
            )
        self._run_bg("SYNTH IMAGE ONLY", job)

    def on_process(self):
        if not self.file_path or not self.file_path.exists():
            messagebox.showwarning("No file", "Open a FITS/SER/PNG first.")
            return
        res = self.res_var.get() or "4K"
        if not self._gate("process", resolution=res):
            return
        t = self.time_var.get().strip()
        # Time may be blank if FITS/filename embeds UTC (e.g. 2026-01-09-1540_…)
        # Process full ALWAYS auto + by-eye (WinJUPOS discipline)
        choice = self._prompt_human_choice(
            force=True,
            image_path=self.file_path,
            title="Process full: AUTO limb (green) + BY EYE (cyan)",
        )
        if choice is None:
            return
        # If user clicked "Automatic only" in dialog, still record dual slot as auto-only
        if not choice.enabled:
            from human_choice import HumanChoice
            choice = HumanChoice(enabled=False, use_as_publish=False)
        hc = choice.to_dict()
        # Prefer human enabled for process unless they chose auto-only
        if hc.get("enabled") is False:
            pass
        else:
            hc["enabled"] = True
            hc["use_as_publish"] = True

        def job():
            from desktop_pipeline import run_process_full
            return run_process_full(
                self.file_path,
                BASE / "outputs",
                user_time=t,
                time_error=self._time_error(),
                mc_iter=self._mc(),
                injection_trials=self._inj(),
                factory_mode=False,
                use_vlbi=self.vlbi_var.get(),
                use_nn=self.nn_var.get(),
                nasa=self.nasa_var.get(),
                aperture_m=self._aperture(),
                cm_override=self._float_opt(self.cm_var),
                sub_lat_override=self._float_opt(self.sublat_var),
                north_pa_override=self._float_opt(self.pa_var),
                winjupos_path=None,
                use_horizons=self.horizons_var.get(),
                use_spice=self.spice_var.get(),
                run_imaging=False,
                winjupos_manual_lon=self._float_opt(self.wj_lon_var),
                winjupos_manual_lat=self._float_opt(self.wj_lat_var),
                human_choice=hc,
            )
        self._run_bg("PROCESS FULL (auto+hand)", job)

    def on_ephemeris(self):
        t = self.time_var.get().strip()
        if not t:
            messagebox.showwarning("Time required", "Enter observation time.")
            return

        def job():
            from ephemeris_pro import resolve_pro_ephemeris, write_ephemeris_report
            from desktop_pipeline import format_full_report
            pe = resolve_pro_ephemeris(
                t,
                time_error_seconds=self._time_error(),
                cm_override=self._float_opt(self.cm_var),
                sub_lat_override=self._float_opt(self.sublat_var),
                north_pa_override=self._float_opt(self.pa_var),
                winjupos_path=self.winjupos_path,
                use_horizons=self.horizons_var.get(),
                use_spice=self.spice_var.get(),
            )
            out = BASE / "outputs" / f"eph_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            out.mkdir(parents=True, exist_ok=True)
            write_ephemeris_report(out / "pro_ephemeris.json", pe)
            pkg = {
                "mode": "ephemeris",
                "pro_ephemeris": pe.to_dict(),
                "headline": {
                    "mode": "ephemeris",
                    "user_time": t,
                    "cm_iii_deg": pe.cm_iii_deg,
                    "cm_source": pe.cm_source,
                    "distance_au": pe.distance_au,
                    "output_dir": str(out),
                },
                "output_dir": str(out),
            }
            pkg["text"] = format_full_report(pkg)
            return pkg
        self._run_bg("EPHEMERIS", job)

    def on_multi(self):
        def job():
            from multi_epoch import load_epochs_from_dir, build_differential_series, write_multi_epoch_report
            from desktop_pipeline import format_full_report
            epochs = load_epochs_from_dir(BASE / "outputs")
            if len(epochs) < 2:
                raise RuntimeError(f"Need ≥2 measured epochs (found {len(epochs)}). Run synthetic/process first.")
            series = build_differential_series(epochs, smooth=True)
            out = BASE / "outputs" / f"multi_{uuid.uuid4().hex[:8]}"
            out.mkdir(parents=True, exist_ok=True)
            write_multi_epoch_report(out / "multi_epoch.json", series, epochs)
            multi = {
                "n": len(epochs),
                "drift_lon_deg_per_day": series.drift_lon_deg_per_day,
                "drift_lon_sigma": series.drift_lon_sigma,
                "rms_residual_sky_arcsec": series.rms_residual_sky_arcsec,
                "smoother": series.smoother,
                "series": series.to_dict(),
            }
            pkg = {
                "mode": "multi_epoch",
                "multi_epoch": multi,
                "headline": {
                    "mode": "multi_epoch",
                    "multi_epoch_n": len(epochs),
                    "drift_lon_deg_per_day": series.drift_lon_deg_per_day,
                    "output_dir": str(out),
                },
                "output_dir": str(out),
            }
            pkg["text"] = format_full_report(pkg)
            return pkg
        self._run_bg("MULTI-EPOCH", job)

    def on_hard(self):
        def job():
            from hard_synth_suite import run_hard_synth_suite
            from desktop_pipeline import format_full_report
            out = BASE / "outputs" / f"hard_{uuid.uuid4().hex[:8]}"
            rep = run_hard_synth_suite(
                out,
                resolution="1080p" if self.res_var.get() in ("16K", "8K", "auto") else (self.res_var.get() or "1080p"),
                injection_trials=min(self._inj(), 12),
                mc_iter=min(self._mc(), 16),
            )
            hard = {
                "calibration_grade": rep.get("calibration_grade"),
                "overall": rep.get("overall"),
                "by_family": rep.get("by_family"),
                "results": rep.get("results"),
            }
            pkg = {
                "mode": "hard_synth",
                "hard_synth": hard,
                "headline": {
                    "mode": "hard_synth",
                    "grade": rep.get("calibration_grade"),
                    "calibration_grade": rep.get("calibration_grade"),
                    "output_dir": str(out),
                },
                "output_dir": str(out),
            }
            pkg["text"] = format_full_report(pkg)
            return pkg
        self._run_bg("HARD-SYNTH", job)

    def on_factory(self):
        res = self.res_var.get() or "4K"
        if not self._gate("factory", resolution=res):
            return
        def job():
            from desktop_pipeline import run_factory_night_full
            return run_factory_night_full(
                BASE / "outputs",
                session_time=self.time_var.get().strip() or "",
                region=self.region_var.get(),
                resolution=res,
                mc_iter=self._mc(),
                injection_trials=self._inj(),
                run_hard=self.hard_in_factory_var.get(),
                aperture_m=self._aperture(),
            )
        self._run_bg("FACTORY NIGHT", job)

    def _nn_epochs(self) -> int:
        try:
            return max(1, min(5000, int(float(self.nn_epochs_var.get().strip() or "20"))))
        except Exception:
            return 20

    def _nn_samples(self) -> int:
        try:
            return max(1, min(200, int(float(self.nn_samples_var.get().strip() or "16"))))
        except Exception:
            return 16

    def _nn_lr(self) -> float:
        try:
            v = float(self.nn_lr_var.get().strip() or "0.01")
            return max(1e-5, min(0.5, v))
        except Exception:
            return 0.01

    def _nn_hours(self) -> float:
        try:
            return max(0.05, min(72.0, float(self.nn_hours_var.get().strip() or "8")))
        except Exception:
            return 8.0

    def _nn_cache(self) -> int:
        try:
            return max(16, min(512, int(float(self.nn_cache_var.get().strip() or "128"))))
        except Exception:
            return 128

    def on_nn_stop(self):
        try:
            import nn_grs
            nn_grs.request_train_stop()
            CONSOLE.warn("Stop requested — training will halt after the current step and save.")
            if hasattr(self, "nn_lbl"):
                self.nn_lbl.configure(text="NN: stop requested…", fg=WARN)
        except Exception as e:
            messagebox.showerror("Stop training", str(e))

    def on_nn_train(self):
        epochs = self._nn_epochs()
        samples = self._nn_samples()
        lr = self._nn_lr()
        hours = self._nn_hours()
        cache = self._nn_cache()
        overnight = bool(self.nn_overnight_var.get())
        resume = bool(self.nn_resume_var.get())

        if overnight:
            ok = messagebox.askokcancel(
                "Overnight training",
                f"Train SPIRE-Net for up to {hours:.1f} hours while you sleep?\n\n"
                f"Samples/epoch: {samples}\n"
                f"RAM cache: {cache} maps\n"
                f"Resume checkpoint: {resume}\n\n"
                "Keep this window open and prevent Mac sleep.\n"
                "If loss stalls, methods switch automatically.",
            )
            if not ok:
                return

        def job():
            import importlib
            import nn_grs
            nn_grs = importlib.reload(nn_grs)
            CONSOLE.info("SPIRE-Net training…")
            if overnight:
                CONSOLE.info(
                    f"OVERNIGHT hours={hours} samples/ep={samples} cache={cache} resume={resume}"
                )
                hist = nn_grs.overnight_train(
                    hours=hours,
                    max_epochs=5000,
                    samples_per_epoch=samples,
                    base_lr=lr,
                    seed=0,
                    use_existing=True,
                    resume=resume,
                    plateau_patience=8,
                    plateau_min_delta=1e-4,
                    sample_cache_size=cache,
                    prevent_sleep=True,
                )
            else:
                CONSOLE.info(f"QUICK epochs={epochs} samples/ep={samples} lr={lr}")
                hist = nn_grs.auto_train(
                    epochs=epochs,
                    samples_per_epoch=samples,
                    prevent_sleep=True,
                    lr=lr,
                    seed=0,
                    use_existing=True,
                )
            if isinstance(hist, dict) and not hist.get("ok", True):
                raise RuntimeError(hist.get("error") or "SPIRE-Net train failed")

            init_l = hist.get("initial_loss") if isinstance(hist, dict) else None
            fin_l = hist.get("final_loss") if isinstance(hist, dict) else None
            best_l = hist.get("best_loss") if isinstance(hist, dict) else None
            gain = hist.get("improvement") if isinstance(hist, dict) else None
            gain_pct = hist.get("improvement_pct") if isinstance(hist, dict) else None
            switches = hist.get("strategy_switches") if isinstance(hist, dict) else None

            report_lines = [
                "SPIRE-NET TRAINING REPORT",
                "=" * 44,
                f"Mode:              {'OVERNIGHT' if overnight else 'QUICK'}",
                f"Epochs / ran:      {epochs if not overnight else hist.get('epochs_ran')}",
                f"Samples per epoch: {samples}",
                f"Learning rate:     {lr}",
                f"Hours target:      {hours if overnight else '—'}",
                f"Elapsed:           {hist.get('elapsed_s') if isinstance(hist, dict) else '?'} s",
                f"Strategy switches: {switches if overnight else '—'}",
                "",
                f"Starting loss:     {init_l}",
                f"Final loss:        {fin_l}",
                f"Best loss:         {best_l}",
                f"Gain (loss drop):  {gain}   ← positive = better",
                f"Gain percent:      {gain_pct}%",
                "",
                f"Weights: {hist.get('weights') if isinstance(hist, dict) else ''}",
                "",
                "Lower loss = better. Gain = start − end.",
                "Overnight switches methods when stuck (plateau).",
            ]
            if isinstance(hist, dict) and hist.get("history"):
                report_lines.append("")
                report_lines.append("Recent epochs (last 40):")
                for row in hist["history"][-40:]:
                    g = row.get("gain_from_start")
                    gp = row.get("gain_pct_from_start")
                    strat = row.get("strategy", "")
                    extra = f"  [{strat}]" if strat else ""
                    if g is not None and gp is not None:
                        extra += f"  gain={g:+.5f} ({gp:+.1f}%)"
                    report_lines.append(
                        f"  ep {row.get('epoch')}: loss={row.get('loss')}{extra}"
                    )
            report_text = "\n".join(report_lines)
            pkg = {
                "mode": "nn_train_overnight" if overnight else "nn_train",
                "headline": {
                    "mode": "nn_train",
                    "grade": "TRAINED",
                    "output_dir": str(BASE / "models"),
                    "initial_loss": init_l,
                    "final_loss": fin_l,
                    "best_loss": best_l,
                    "improvement": gain,
                    "improvement_pct": gain_pct,
                },
                "nn": hist if isinstance(hist, dict) else {"history": hist},
                "text": report_text,
            }
            self.msg_q.put(("status", "NN DONE"))
            self.msg_q.put(("nn_report", {
                "summary": hist.get("summary") if isinstance(hist, dict) else "",
                "initial_loss": init_l,
                "final_loss": fin_l,
                "improvement": gain,
                "improvement_pct": gain_pct,
            }))
            return pkg

        self._run_bg("NN OVERNIGHT" if overnight else "NN TRAIN", job)

        def poll_nn():
            try:
                import nn_grs
                st = nn_grs.get_train_status()
                if st.get("running"):
                    ep = st.get("epoch")
                    eps = st.get("epochs")
                    loss = st.get("loss")
                    gain = st.get("improvement")
                    gp = st.get("improvement_pct")
                    strat = st.get("strategy") or ""
                    left = st.get("hours_left")
                    line = f"NN train ep {ep}/{eps}  loss={loss}"
                    if strat:
                        line += f"  [{strat}]"
                    if left is not None:
                        line += f"  left={float(left):.2f}h"
                    if gain is not None and gp is not None:
                        try:
                            line += f"  gain={float(gain):+.5f} ({float(gp):+.1f}%)"
                        except Exception:
                            pass
                    self.nn_lbl.configure(text=line, fg=WARN)
                    if hasattr(self, "nn_gain_lbl") and st.get("initial_loss") is not None:
                        best = st.get("best_loss")
                        self.nn_gain_lbl.configure(
                            text=(
                                f"Loss: start={st.get('initial_loss')}  now={loss}  "
                                f"best={best}  gain={gain}  switches={st.get('strategy_switches')}"
                            ),
                            fg=FG,
                        )
                    self.after(1000, poll_nn)
                else:
                    ready = bool(st.get("trained") or st.get("weights_exist"))
                    msg = str(st.get("message") or "")
                    if ready and msg.startswith("error:"):
                        msg = "weights ready"
                    self.nn_lbl.configure(
                        text=f"NN: {'ready' if ready else 'idle'} · {msg}",
                        fg=OK if ready else MUTED,
                    )
                    if hasattr(self, "nn_gain_lbl") and st.get("final_loss") is not None:
                        g = st.get("improvement")
                        gp = st.get("improvement_pct")
                        self.nn_gain_lbl.configure(
                            text=(
                                f"Loss report: start={st.get('initial_loss')} → "
                                f"end={st.get('final_loss')}  best={st.get('best_loss')}  "
                                f"gain={g} ({gp}%)"
                            ),
                            fg=OK if (isinstance(g, (int, float)) and g >= 0) else ERR,
                        )
            except Exception as e:
                self.nn_lbl.configure(text=f"NN: status error · {e}", fg=ERR)

        self.after(500, poll_nn)

    # ── new experimental stack / finetune handlers ──────────────────────
    def on_nn_finetune(self):
        """Short fine-tune pass — writes spire_net_finetuned.npz (does NOT touch shipped weights)."""
        def job():
            from spire_finetune import run_finetune
            diag = run_finetune()
            txt = (
                "SPIRE-Net short fine-tune complete.\n\n"
                f"  Started from:        {diag.get('started_from')}\n"
                f"  Samples:             {diag.get('n_samples')}\n"
                f"  Epochs:              {diag.get('epochs')}\n"
                f"  Learning rate:       {diag.get('lr')}\n"
                f"  Initial loss:        {diag.get('initial_loss'):.5f}\n"
                f"  Final loss:          {diag.get('final_loss'):.5f}\n"
                f"  Improvement:         {diag.get('improvement_pct'):+.2f}%\n"
                f"  Elapsed:             {diag.get('elapsed_s'):.1f} s\n"
                f"  Output:              {diag.get('out_path')}\n\n"
                "The shipped weights are untouched. To use the fine-tuned weights,\n"
                "set GRS_USE_FINETUNED=1 in your environment before launching the app.\n"
            )
            return {"text": txt}
        self._run_bg("NN Fine-Tune", job)

    def on_planetary_derotate(self):
        """Re-run the publish on the current frame with explicit zonal derotation.

        This is a thin convenience over the existing champion path: it just
        re-runs `run_process_full` with the current file/time so you can
        re-process without re-loading the file. For a true SER-video
        derotation, see the 5D / 10D / Inf buttons below.
        """
        if not getattr(self, "file_path", None):
            messagebox.showinfo("No file", "Open a FITS/SER/PNG file first.")
            return
        from cli import main as _cli_main  # noqa: F401

        def job():
            # Re-process with the currently-loaded file and the time in the UI
            t = (self.time_var.get() or "").strip()
            from product_core import process_image
            from product_core import default_out_root
            out = default_out_root() / "derotate"
            out.mkdir(parents=True, exist_ok=True)
            pkg = process_image(
                str(self.file_path),
                t or "1970-01-01 00:00:00",
                out_root=out,
                use_nn=False,
            )
            return pkg
        self._run_bg("Derotate (WinJUPOS-style)", job)

    def _on_stack_engine(self, engine: str):
        """Common helper: synthesise a tiny video, run the chosen stacker."""
        def job():
            from synthetic_hq import SynthSpec, generate
            from datetime import datetime, timezone
            from pathlib import Path
            import os, glob

            out_root = Path(os.environ.get(
                "GRS_STACK_OUT", str((Path(__file__).resolve().parent / "outputs" / "stack_runs"))
            ))
            out_root.mkdir(parents=True, exist_ok=True)
            run_dir = out_root / f"{engine}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            run_dir.mkdir(parents=True, exist_ok=True)
            tmp = run_dir / "frames"
            tmp.mkdir(exist_ok=True)
            n_frames = 24
            frames = []
            for k in range(n_frames):
                spec = SynthSpec(
                    user_time_iso="",
                    region="global",
                    resolution_preset="720p",   # small for speed
                    random_time=True,
                    seed=100000 + k * 31,
                    mode="metrology",
                    write_grs_crop=False,
                )
                _png, fit, _truth = generate(spec, tmp)
                import grs_complete_system as grs
                arr, _ = grs.read_fits(fit)
                img = np.asarray(arr, dtype=np.float64)
                if img.ndim == 3 and img.shape[0] == 3:
                    img = 0.3 * img[0] + 0.5 * img[1] + 0.2 * img[2]
                frames.append(img)
            if engine == "jpa_10k":
                from jpa_10k import run_jpa_10k
                res = run_jpa_10k(frames, run_dir)
            elif engine == "jpa_10d":
                from jpa_10d import run_jpa_10d
                res = run_jpa_10d(frames, run_dir)
            elif engine == "jpa_inf":
                from jupiter_infinite_tensor_engine import run_jpa_inf
                res = run_jpa_inf(frames, run_dir)
            else:
                raise ValueError(f"unknown engine: {engine}")
            txt = (
                f"{engine} done.\n"
                f"  frames:           {res.n_frames}\n"
                f"  APs:              {res.n_aps}\n"
                f"  mean drift RMS:   {res.mean_rms_drift_px:.3f} px\n"
                f"  elapsed:          {res.elapsed_s:.1f} s\n"
                f"  stacked PNG:      {res.output_path}\n"
            )
            for n in res.notes:
                txt += f"  - {n}\n"
            return {"text": txt, "preview": res.output_path}
        self._run_bg(f"Stack ({engine})", job)

    def on_stack_5d(self):
        self._on_stack_engine("jpa_10k")

    def on_stack_10d(self):
        self._on_stack_engine("jpa_10d")

    def on_stack_inf(self):
        self._on_stack_engine("jpa_inf")



def main():
    """Free open — no login passcode."""
    import os
    os.environ["GRS_REQUIRE_LOGIN"] = "0"
    app = GRSDesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()
