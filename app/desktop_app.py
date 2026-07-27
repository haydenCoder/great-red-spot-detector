#!/usr/bin/env python3
"""
GRS Observatory — native macOS desktop app (process-focused).

Core workflow: open image → set UTC → Process (auto + by-eye limb) → publish.
SPIRE-Net weights are shipped frozen (inference only — no training UI).
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
import uuid
from datetime import datetime
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
    """Locate the only user guide (pure helper — no Tk required)."""
    cands = [
        code.parent / "docs" / "GRS_OBSERVATORY_BOOK.md",
        code / "docs" / "GRS_OBSERVATORY_BOOK.md",
        base / "docs" / "GRS_OBSERVATORY_BOOK.md",
        code.parent / "docs" / "GRS_OBSERVATORY_BOOK.html",
        base / "docs" / "GRS_OBSERVATORY_BOOK.html",
    ]
    for p in cands:
        if p.exists():
            return p
    return None


def resolve_buttons_doc_path(code: Path, base: Path) -> Optional[Path]:
    """
    Locate the button → function guide.

    Prefers dedicated HTML, then the book / desktop reference docs.
    Pure helper — no Tk required (unit-testable).
    """
    names = (
        "BUTTON_GUIDE.html",
        "button_guide.html",
        "BUTTONS.html",
        "features/button_guide.html",
        "features/BUTTON_GUIDE.html",
        "GRS_OBSERVATORY_BOOK.html",
        "GRS_OBSERVATORY_BOOK.md",
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

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

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


# Light, readable UI — black labels, grey descriptions (easy to see)
BG = "#e8ecf1"          # app background (soft grey)
PANEL = "#ffffff"       # side panels (white)
PANEL2 = "#f3f5f8"      # secondary surfaces
CARD = "#ffffff"        # metric cards
FG = "#111827"          # black / near-black words
MUTED = "#4b5563"       # grey descriptions under buttons
ACCENT = "#2563eb"      # blue primary actions
OK = "#15803d"
WARN = "#ca8a04"
ERR = "#b91c1c"
PURPLE = "#7c3aed"      # factory
BORDER = "#c5cdd8"
INPUT_BG = "#ffffff"
CONSOLE_BG = "#0f172a"  # console stays dark for log contrast
CONSOLE_FG = "#e2e8f0"
BTN_TEXT = "#ffffff"    # text on colored buttons

# Accurate plain-language help for every control / action (shown via ⓘ)
HELP: Dict[str, str] = {
    "app": (
        "GRS Observatory measures the position of Jupiter’s Great Red Spot (GRS)\n"
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
        "Use the VLBI-inspired optical stack:\n"
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
        "If “Measure after synthetic” is on, runs the full VLBI measure and\n"
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
        "2) Synthetic with RANDOM epoch + full VLBI measure\n"
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


class GRSDesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GRS Observatory · Desktop")
        self.geometry("1380x880")
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
        """Only user guide: GRS_OBSERVATORY_BOOK.md"""
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
                "Guide not found.\nExpected: docs/GRS_OBSERVATORY_BOOK.md",
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
                "  docs/GRS_OBSERVATORY_BOOK.md\n"
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
            name, ver, tag = "GRS Observatory", "6.1", "Professional GRS metrology"
        lic_line = ""
        if self._license_status:
            lic_line = f"\nLicense: {self._license_status.plan_label}"
        messagebox.showinfo(
            "About",
            f"{name} v{ver}\n{tag}{lic_line}\n\n"
            "Ground-based optical metrology for Jupiter’s Great Red Spot.\n"
            "Publish: GS-MAP · CM: SPICE / Horizons / WinJUPOS\n"
            "Only guide: docs/GRS_OBSERVATORY_BOOK.md\n"
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
        # Light theme: black words, grey secondary, white panels
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
            padding=[16, 10],
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
            thickness=8,
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
        # Header bar
        head = tk.Frame(self, bg=BG, highlightthickness=0)
        head.pack(fill=tk.X, padx=16, pady=(14, 8))
        left_h = tk.Frame(head, bg=BG)
        left_h.pack(side=tk.LEFT, fill=tk.X, expand=True)
        title_row = tk.Frame(left_h, bg=BG)
        title_row.pack(anchor=tk.W)
        logo = tk.Label(
            title_row, text="♃", bg=PANEL, fg=ACCENT,
            font=("Helvetica", 18, "bold"), width=3, padx=4, pady=4,
            highlightbackground=BORDER, highlightthickness=1,
        )
        logo.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(
            title_row, text="GRS Observatory", bg=BG, fg=FG,
            font=("Helvetica", 20, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            left_h,
            text="Black labels · grey help under every button · no question-mark icons",
            bg=BG, fg=MUTED, font=("Helvetica", 12),
        ).pack(anchor=tk.W, pady=(4, 0))

        right_h = tk.Frame(head, bg=BG)
        right_h.pack(side=tk.RIGHT)
        self.license_var = tk.StringVar(value="● Evaluation")
        self.license_lbl = tk.Label(
            right_h, textvariable=self.license_var, bg=PANEL, fg=MUTED,
            font=("Helvetica", 11, "bold"), padx=12, pady=6,
            highlightbackground=BORDER, highlightthickness=1,
        )
        self.license_lbl.pack(side=tk.RIGHT, padx=6)
        self.status_var = tk.StringVar(value="● IDLE")
        self.status_lbl = tk.Label(
            right_h, textvariable=self.status_var, bg=PANEL, fg=OK,
            font=("Helvetica", 12, "bold"), padx=14, pady=6,
            highlightbackground=BORDER, highlightthickness=1,
        )
        self.status_lbl.pack(side=tk.RIGHT, padx=6)
        self.prog = ttk.Progressbar(right_h, mode="indeterminate", length=140)
        self.prog.pack(side=tk.RIGHT, padx=8)
        self._refresh_license_badge()
        
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
        # Fixed production defaults (UI simplified — no train / factory / hard-synth)
        self.vlbi_var = tk.BooleanVar(value=True)       # multi-method optical stack
        self.factory_var = tk.BooleanVar(value=False)
        self.nasa_var = tk.BooleanVar(value=True)       # geometry report only
        self.nn_var = tk.BooleanVar(value=False)      # frozen weights optional off by default
        self.imaging_var = tk.BooleanVar(value=False)
        self.synth_process_var = tk.BooleanVar(value=False)
        self.hard_in_factory_var = tk.BooleanVar(value=False)
        self.dual_var = tk.BooleanVar(value=True)
        self._check(left, "Full multi-method stack + error budget", self.vlbi_var,
                    "Multi-method optical measure (not radio interferometry).")
        self._check(left, "Write Horizons geometry report", self.nasa_var,
                    "Planet geometry only — not an official NASA GRS longitude.")
        self._check(left, "Use frozen SPIRE-Net weights (optional hint)", self.nn_var,
                    "Loads app/models/spire_net_weights.npz only. Training removed.")

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
            left, text="SPIRE-Net: frozen weights on disk (no training UI)",
            bg=PANEL, fg=MUTED, font=("Helvetica", 11), wraplength=340, justify=tk.LEFT,
        )
        self.nn_lbl.pack(anchor=tk.W, padx=14, pady=2)
        self.nn_gain_lbl = tk.Label(
            left, text="",
            bg=PANEL, fg=FG, font=("Helvetica", 11), wraplength=340, justify=tk.LEFT,
        )
        self.nn_gain_lbl.pack(anchor=tk.W, padx=14, pady=(0, 4))
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

        # Metric strip
        metrics = tk.Frame(center, bg=BG)
        metrics.pack(fill=tk.X, pady=(0, 8))
        self.metric_vars = {}
        mhead = tk.Frame(metrics, bg=BG)
        mhead.pack(fill=tk.X)
        tk.Label(
            mhead, text="RESULT STRIP", bg=BG, fg=FG,
            font=("Helvetica", 11, "bold"),
        ).pack(side=tk.LEFT)
        mrow = tk.Frame(metrics, bg=BG)
        mrow.pack(fill=tk.X, pady=(6, 0))
        for key, label in (
            ("grade", "Grade"),
            ("lon", "Lon III"),
            ("lat", "Lat"),
            ("sigma", "σ_tot ″"),
            ("truth", "Truth ″"),
            ("epoch", "Epoch"),
        ):
            card = tk.Frame(
                mrow, bg=CARD,
                highlightbackground=BORDER, highlightthickness=1,
            )
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)
            tk.Label(
                card, text=label.upper(), bg=CARD, fg=MUTED,
                font=("Helvetica", 10, "bold"),
            ).pack(anchor=tk.W, padx=10, pady=(8, 0))
            v = tk.StringVar(value="—")
            self.metric_vars[key] = v
            tk.Label(
                card, textvariable=v, bg=CARD, fg=FG,
                font=("Menlo", 13, "bold"),
            ).pack(anchor=tk.W, padx=10, pady=(2, 10))

        self.nb = ttk.Notebook(center)
        self.nb.pack(fill=tk.BOTH, expand=True)

        # Preview tab
        tab_prev = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(tab_prev, text="  Preview  ")
        self.preview_lbl = tk.Label(
            tab_prev,
            text="No image yet\n\nUse  Generate Synthetic  or  Open file",
            bg=PANEL2, fg=MUTED, font=("Helvetica", 14),
            highlightbackground=BORDER, highlightthickness=1,
        )
        self.preview_lbl.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Results tab
        tab_res = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(tab_res, text="  Full Results  ")
        self.results = scrolledtext.ScrolledText(
            tab_res, bg=CONSOLE_BG, fg=CONSOLE_FG, insertbackground=CONSOLE_FG,
            font=("Menlo", 12), wrap=tk.WORD, relief=tk.FLAT, borderwidth=0,
            highlightthickness=0, padx=8, pady=8,
        )
        self.results.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Dashboard tab
        tab_dash = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(tab_dash, text="  Dashboard  ")
        self.dash = scrolledtext.ScrolledText(
            tab_dash, bg=CONSOLE_BG, fg=CONSOLE_FG, font=("Menlo", 12),
            wrap=tk.WORD, relief=tk.FLAT, padx=8, pady=8,
        )
        self.dash.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.dash.insert(tk.END, "Run a job to populate the dashboard.\n")

        # ── Console ──
        right = tk.Frame(
            body, bg=PANEL, width=340,
            highlightbackground=BORDER, highlightthickness=1,
        )
        right.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(10, 0))
        right.pack_propagate(False)
        ch = tk.Frame(right, bg=PANEL)
        ch.pack(fill=tk.X, padx=10, pady=(10, 4))
        tk.Label(
            ch, text="LIVE CONSOLE", bg=PANEL, fg=FG,
            font=("Helvetica", 11, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            ch, text="scroll for log", bg=PANEL, fg=MUTED,
            font=("Helvetica", 10),
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

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill=tk.X, padx=16, pady=8)
        tk.Label(
            foot,
            text=f"Data · {BASE}   ·   grey text = help under buttons   ·   black text = labels",
            bg=BG, fg=MUTED, font=("Helvetica", 11),
        ).pack(anchor=tk.W)

    def _section(self, parent, title: str):
        """Section title: black bold on light grey strip."""
        wrap = tk.Frame(parent, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
        wrap.pack(fill=tk.X, padx=12, pady=(16, 6))
        tk.Label(
            wrap, text=title.upper(), bg=PANEL2, fg=FG,
            font=("Helvetica", 11, "bold"),
        ).pack(anchor=tk.W, padx=12, pady=9)

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
            bg=INPUT_BG, fg=FG, insertbackground=FG,
            relief=tk.FLAT, font=("Menlo", 12),
            highlightbackground=BORDER, highlightthickness=1,
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
        wrap.pack(fill=tk.X, padx=12, pady=(6, 2))
        if secondary:
            b = tk.Button(
                wrap, text=text, command=cmd,
                bg=PANEL2, fg=FG,
                activebackground="#e5e7eb", activeforeground=FG,
                relief=tk.FLAT, font=("Helvetica", 12, "bold"),
                padx=12, pady=10, cursor="hand2",
                highlightbackground=BORDER, highlightthickness=1,
            )
        else:
            b = tk.Button(
                wrap, text=text, command=cmd,
                bg=color, fg=BTN_TEXT,
                activebackground=color, activeforeground=BTN_TEXT,
                relief=tk.FLAT, font=("Helvetica", 13, "bold"),
                padx=12, pady=12, cursor="hand2",
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
            self.status_lbl.configure(fg=OK if status == "DONE" else (ERR if status == "ERROR" else MUTED))
            try:
                self.prog.stop()
            except Exception:
                pass

    def _log_ui(self, level: str, msg: str):
        tag = level if level in ("OK", "WARN", "ERROR", "INFO", "DEBUG") else "INFO"
        self.console.insert(tk.END, f"[{level}] {msg}\n", tag)
        self.console.see(tk.END)

    def _results(self, text: str):
        self.results.delete("1.0", tk.END)
        self.results.insert(tk.END, text)
        self.nb.select(1)

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

        # Dashboard summary — publish first
        self.dash.delete("1.0", tk.END)
        ch = package.get("champion") or {}
        sd = package.get("superduper") or {}
        sdr = (sd.get("report_this") or {})
        lines = [
            "SUPERDUPER / PUBLISH THIS (official)",
            "=" * 40,
            f"grade:      {ch.get('grade') or h.get('champion_grade') or grade}",
            f"unbeatable: {ch.get('unbeatable_auto') or h.get('unbeatable_auto')}",
            f"ultimate:   {h.get('ultimate_lock_pass')}/{h.get('ultimate_lock_total')} gates",
            f"definition: {pub.get('publish_definition') or h.get('publish_definition')}",
            f"lon_iii:    {lon}  (centre — use GS-MAP / champion for WJ compare)",
            f"lat:        {lat}",
            f"lat_graphic:{pub.get('publish_lat_planetographic_deg') or h.get('lat_planetographic_deg')}",
            f"σ_sky:      {pub.get('publish_sigma_sky_arcsec') or h.get('champion_sigma_sky_arcsec')}",
            f"length:     {h.get('length_deg') or pub.get('length_deg') or ch.get('extent_ew_deg')}",
            f"W edge:     {h.get('west_edge_lon_iii_deg')}",
            f"E edge:     {h.get('east_edge_lon_iii_deg')}",
            f"extent:     {h.get('extent_lon_deg') or ch.get('extent_ew_deg')} °",
            f"cm_source:  {pub.get('cm_source') or h.get('cm_source')}",
            f"absolute:   {pub.get('absolute_ok') if pub.get('absolute_ok') is not None else ch.get('absolute_publish_ok')}",
            f"WinJUPOS:   {eq.get('agreement') or h.get('winjupos_agreement') or '—'}",
            f"equal_WJ:   {eq.get('equal_to_winjupos')}",
            f"vs_WJ_sky:  {eq.get('sky_error_arcsec')}",
            f"soup:       {pub.get('soup_n_methods') or h.get('soup_n_methods')} methods = scatter only",
            f"citation:   {sdr.get('citation_line') or h.get('superduper_citation') or h.get('citation_line') or '—'}",
            "",
            "Open SUPERDUPER_BEST_ANSWER.txt in the job folder for the one-page card.",
            "",
        ]
        dual = package.get("dual_measure") or {}
        if dual:
            a = dual.get("automatic") or {}
            hu = dual.get("human") or {}
            cmp_ = dual.get("comparison") or {}
            lines += [
                "DUAL MEASURE (auto + human)",
                "=" * 40,
                f"official:   {dual.get('official')}",
                f"auto lon:   {a.get('lon_iii_deg')}  ({a.get('publish_definition')})",
                f"human lon:  {hu.get('lon_iii_deg')}  ({hu.get('publish_definition')})",
                f"Δsky:       {cmp_.get('sky_delta_arcsec')} ″  ({cmp_.get('agreement')})",
                f"note:       {cmp_.get('note')}",
                "",
            ]
        lines += [
            "FULL HEADLINE",
            "-" * 40,
        ]
        for k, v in (h or {}).items():
            lines.append(f"{k}: {v}")
        if package.get("error_budget"):
            lines += ["", "ERROR BUDGET", "-" * 20]
            eb = package["error_budget"]
            comps = eb.get("components_sky_arcsec") if isinstance(eb, dict) else None
            if isinstance(comps, dict):
                for k, v in comps.items():
                    lines.append(f"  {k}: {v}")
            else:
                lines.append(json.dumps(eb, indent=2, default=str)[:2000])
        self.dash.insert(tk.END, "\n".join(lines))

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
                        messagebox.showerror("GRS Observatory", str(payload["error"]))
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
            initialfile=f"grs_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
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
            out = BASE / "outputs" / f"eph_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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
        messagebox.showinfo(
            "Training removed",
            "SPIRE-Net training is disabled in this release.\n"
            "Frozen weights in app/models/spire_net_weights.npz are used as-is.",
        )
        return
        # legacy train path retained below but unreachable

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



def main():
    """Free open — no login passcode."""
    import os
    os.environ["GRS_REQUIRE_LOGIN"] = "0"
    app = GRSDesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()
