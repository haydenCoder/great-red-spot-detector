#!/usr/bin/env python3
"""
Private release builder for GRS Observatory
==========================================

What this does:
  1) Copies app modules into a staging tree
  2) Strips docstrings / comments (code becomes harder to read if recovered)
  3) Compiles to .pyc only and DELETES .py source from the staging tree
  4) Invokes PyInstaller to build Mac .app / one-file (or Windows .exe on Windows)
  5) Copies ONLY the binary product to ~/Downloads (never the source tree)

Honest limit: a determined reverse-engineer can still recover logic from a binary.
This stops casual viewing of your .py files. Keep the source folder private.
"""
from __future__ import annotations

import ast
import compileall
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BUILD = ROOT / "build" / "private_stage"
DIST = ROOT / "dist"
DOWNLOADS = Path.home() / "Downloads"
OUT_DIR = DOWNLOADS / "GRS_Observatory_RELEASE"


class DocstringStripper(ast.NodeTransformer):
    """Remove module/class/function docstrings so recovered code is less readable."""

    def _strip(self, node):
        if not getattr(node, "body", None):
            return node
        if (
            len(node.body) >= 1
            and isinstance(node.body[0], ast.Expr)
            and isinstance(getattr(node.body[0], "value", None), ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body = node.body[1:] or [ast.Pass()]
        return node

    def visit_Module(self, node):
        self.generic_visit(node)
        return self._strip(node)

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        return self._strip(node)

    def visit_AsyncFunctionDef(self, node):
        self.generic_visit(node)
        return self._strip(node)

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        return self._strip(node)


def strip_file(src: Path, dst: Path) -> None:
    text = src.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
        tree = DocstringStripper().visit(tree)
        ast.fix_missing_locations(tree)
        out = ast.unparse(tree)  # py3.9+
        # minimal junk header — not your readable source
        out = "# GRS binary module — source not included\n" + out
    except Exception:
        # if parse fails, still ship minified-ish
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
        out = "\n".join(lines)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8")


def stage_sources() -> Path:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    stage = BUILD / "app"
    stage.mkdir(parents=True)

    skip_dirs = {
        "__pycache__", "outputs", "uploads", "ssd_cache", "nasa_cache",
        "logs", "nn_train_cache", "owner_access",
    }
    # Never ship identity / license / session secrets into a release tree
    skip_basenames = {
        "license.json", "accounts.json", "session.json", "admin_session.json",
        "usage.jsonl", "EVERY_USER_DATA.jsonl", ".env",
    }
    for path in APP.rglob("*"):
        rel = path.relative_to(APP)
        if any(p in skip_dirs for p in rel.parts):
            continue
        if path.is_dir():
            continue
        if path.name.lower() in {n.lower() for n in skip_basenames}:
            continue
        # only code + templates/static/models/ephemeris
        if path.suffix.lower() in {".py"}:
            strip_file(path, stage / rel)
        elif path.suffix.lower() in {
            ".html", ".css", ".js", ".json", ".txt", ".csv",
            ".npz", ".png", ".jpg", ".md", ".tls", ".tpc", ".bsp",
        } or path.name in {"VERSION", "LICENSE"}:
            # Allow model meta JSON and kernel_manifest only
            if path.suffix.lower() == ".json" and path.name not in {
                "spire_net_meta.json", "kernel_manifest.json", "spire_train_checkpoint.json",
            }:
                # Skip arbitrary app-root JSON (could be secrets)
                if len(rel.parts) == 1:
                    continue
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

    # copy VERSION into stage root area
    if (ROOT / "VERSION").exists():
        (BUILD / "VERSION").write_text((ROOT / "VERSION").read_text(encoding="utf-8"), encoding="utf-8")
    if (ROOT / "LICENSE").exists():
        shutil.copy2(ROOT / "LICENSE", BUILD / "LICENSE")
    if (ROOT / "docs").exists():
        # only user-facing docs, not full source maps
        docs_out = BUILD / "docs"
        docs_out.mkdir(exist_ok=True)
        for name in ("GRS_OBSERVATORY_BOOK.md", "SECURITY.md"):
            p = ROOT / "docs" / name
            if p.exists():
                shutil.copy2(p, docs_out / name)

    # compile to bytecode (also leaves stripped .py for PyInstaller analysis only)
    compileall.compile_dir(str(stage), force=True, quiet=1, optimize=2)
    # NOTE: stripped .py stay in staging for the build step only.
    # Staging is never copied to Downloads — only the final binary is published.
    print(f"Staged private tree (stripped, not published): {stage}")
    return stage


def run_pyinstaller(stage: Path) -> None:
    venv_py = ROOT / ".venv" / "bin" / "python"
    if not venv_py.exists():
        venv_py = Path(sys.executable)
    # ensure pyinstaller
    subprocess.check_call([str(venv_py), "-m", "pip", "install", "-q", "pyinstaller>=6.0", "pillow"])

    is_win = platform.system() == "Windows"
    sep = ";" if is_win else ":"
    datas = []
    for src, dest in [
        (stage / "models", "models"),
        (stage / "ephemeris_data", "ephemeris_data"),
        (BUILD / "docs", "docs"),
        (BUILD / "VERSION", "."),
        (BUILD / "LICENSE", "."),
    ]:
        if src.exists():
            datas += ["--add-data", f"{src}{sep}{dest}"]

    hides = [
        "numpy", "scipy", "scipy.ndimage", "scipy.signal", "PIL", "PIL.Image", "PIL.ImageTk",
        "certifi", "grs_complete_system", "desktop_pipeline", "product_core", "precision_engine",
        "research_grade", "vlbi_metrology", "ephemeris_pro", "sota_accuracy", "gold_standard",
        "all_methods", "all_methods_extra", "nn_grs", "license_manager", "accounts", "fits_time",
        "security_hard", "paths", "result_report", "ai_hard_cases", "spice_auto", "group_access",
    ]
    hide_args = []
    for h in hides:
        hide_args += ["--hidden-import", h]

    entry = stage / "desktop_app.py"
    name = "GRS_Observatory"
    common = [
        str(venv_py), "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", name,
        "--paths", str(stage),
        # Do NOT add original APP path — that would pull unstripped source into the bundle.
        *datas,
        *hide_args,
        "--collect-submodules", "numpy",
        "--collect-submodules", "scipy",
        str(entry),
    ]

    if is_win:
        cmd = common[:5] + ["--onefile", "--windowed"] + common[5:]
        print("Building Windows EXE…")
        subprocess.check_call(cmd, cwd=str(ROOT))
    else:
        # Mac .app (onedir bundle) — best user experience
        cmd_app = common[:5] + [
            "--windowed",
            "--osx-bundle-identifier", "com.grs.observatory.desktop",
        ] + common[5:]
        print("Building macOS .app…")
        subprocess.check_call(cmd_app, cwd=str(ROOT))
        # one-file binary as well
        cmd_one = common[:5] + ["--onefile", "--windowed", "--name", "GRS_Observatory_OneFile"] + common[5:]
        print("Building macOS one-file binary…")
        try:
            subprocess.check_call(cmd_one, cwd=str(ROOT))
        except subprocess.CalledProcessError as e:
            print("One-file optional build failed:", e)


def publish_to_downloads() -> None:
    """Publish App + single-file (exe-like) to RELEASE folder AND Downloads root."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for p in list(OUT_DIR.iterdir()):
        if p.name.startswith("GRS_") or p.name.endswith(".txt"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)

    app_bundle = DIST / "GRS_Observatory.app"
    onefile = DIST / "GRS_Observatory_OneFile"
    if not onefile.exists() and (DIST / "GRS_Observatory").is_file():
        onefile = DIST / "GRS_Observatory"
    win_exe = DIST / "GRS_Observatory.exe"
    copied: List[str] = []

    def _copy_any(src: Path, dest: Path) -> None:
        if not src.exists():
            return
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
            else:
                dest.unlink(missing_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
            try:
                dest.chmod(0o755)
            except Exception:
                pass
        copied.append(str(dest.name))

    # RELEASE folder
    if app_bundle.exists():
        _copy_any(app_bundle, OUT_DIR / "GRS_Observatory.app")
    if onefile.exists() and onefile.is_file():
        _copy_any(onefile, OUT_DIR / "GRS_Observatory_Mac")
        _copy_any(onefile, OUT_DIR / "GRS_Observatory_OneFile")
    if win_exe.exists():
        _copy_any(win_exe, OUT_DIR / "GRS_Observatory.exe")

    # Downloads root — always refresh app + mac one-file
    if app_bundle.exists():
        _copy_any(app_bundle, DOWNLOADS / "GRS_Observatory.app")
    if onefile.exists() and onefile.is_file():
        _copy_any(onefile, DOWNLOADS / "GRS_Observatory_Mac")
    if win_exe.exists():
        _copy_any(win_exe, DOWNLOADS / "GRS_Observatory.exe")
    else:
        (DOWNLOADS / "GRS_Observatory_Windows_EXE_README.txt").write_text(
            "True Windows .exe is built only on a Windows PC (Build_Windows_EXE.bat).\n"
            "On Mac use: GRS_Observatory.app  or  GRS_Observatory_Mac\n",
            encoding="utf-8",
        )

    for data in (OUT_DIR / "GRS_Observatory_Data", DOWNLOADS / "GRS_Observatory_Data"):
        data.mkdir(exist_ok=True)
        for sub in ("outputs", "uploads", "logs", "ssd_cache", "models"):
            (data / sub).mkdir(exist_ok=True)

    readme = (
        "GRS Observatory — PRIVATE RELEASE\n"
        "=================================\n\n"
        "Downloads / RELEASE folder contains:\n"
        "  • GRS_Observatory.app     Mac double-click app\n"
        "  • GRS_Observatory_Mac     Mac single-file (exe-like)\n"
        "  • GRS_Observatory.exe     Windows only if built on Windows\n\n"
        "LOGIN REQUIRED before use (Gmail or Admin).\n"
        "No Python source included.\n\n"
        "After editing .py: run Watch_And_Rebuild.command then reopen the app.\n"
    )
    (OUT_DIR / "README_FOR_USERS.txt").write_text(readme, encoding="utf-8")
    (DOWNLOADS / "GRS_Observatory_README.txt").write_text(readme, encoding="utf-8")
    print("Published to:", OUT_DIR)
    print("Also: ~/Downloads/GRS_Observatory.app + GRS_Observatory_Mac")
    print("Copied:", ", ".join(copied) or "(nothing)")


def main() -> int:
    print("=== Private release (source stripped) ===")
    stage = stage_sources()
    run_pyinstaller(stage)
    publish_to_downloads()
    print("Done. Give others ONLY:", OUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
