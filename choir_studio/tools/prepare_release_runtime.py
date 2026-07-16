"""Build the Windows-only runtime template bundled by the Tauri installer.

The installed Studio must not depend on the developer checkout or an installed
Python.  This script copies the base CPython distribution, Choir's runtime
packages, DECtalk binaries, and a clean writable song workspace into a Tauri
resource directory.  The directory is intentionally ignored because it is a
large, reproducible release artifact.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = REPO_ROOT / "choir_studio" / "src-tauri" / "resources" / "runtime_template"

ENGINE_FILES = ("choir.py", "generateSpectrograms.py", "say.exe", "dectalk.dll", "dtalk_us.dic", "MSVCRTd.DLL")
ENGINE_DIRECTORIES = ("pyFuncs", "tools", "songs")
RUNTIME_SKIP_DIRECTORIES = {
    "__pycache__",
    # Local development-only GUI packages are not part of Choir Studio's runtime.
    "PySide6",
    "shiboken6",
    "pip",
    "setuptools",
    "pkg_resources",
    "test",
    "tests",
    "idlelib",
    "tcl",
    "Tools",
    "Doc",
}
RUNTIME_SKIP_PREFIXES = ("pyside6", "shiboken6", "pip-", "setuptools-")
ENGINE_SKIP_DIRECTORIES = {"__pycache__", ".git", ".venv", "outputs", "external", "target", "node_modules"}
ENGINE_SKIP_FILES = {"macintalk_standalone.py", "setup_wintalker_backend.ps1"}


def _ignore_runtime(_: str, names: list[str]) -> set[str]:
    skipped: set[str] = set()
    for name in names:
        lowered = name.lower()
        if name in RUNTIME_SKIP_DIRECTORIES or lowered.startswith(RUNTIME_SKIP_PREFIXES) or name == "__pycache__":
            skipped.add(name)
    return skipped


def _ignore_engine(_: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in ENGINE_SKIP_DIRECTORIES
        or name in ENGINE_SKIP_FILES
        or name.endswith((".pyc", ".pyo", ".mmpz", ".mmpz.bak"))
    }


def _copy_tree(source: Path, destination: Path, ignore) -> None:
    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)


def _clear_previous_template(destination: Path) -> None:
    """Clear generated payloads while preserving tracked resource-glob markers."""
    destination.mkdir(parents=True, exist_ok=True)
    preserved = {".gitkeep", "runtime-manifest.placeholder"}
    for path in destination.iterdir():
        if path.name in preserved:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _require(source: Path) -> None:
    if not source.is_file():
        raise RuntimeError(f"Required release file is missing: {source}")


def build_template(destination: Path, *, require_rubberband: bool) -> None:
    venv_site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    base_python = Path(sys.base_prefix)
    if not venv_site_packages.is_dir() or not base_python.joinpath("python.exe").is_file():
        raise RuntimeError("Run this with Choir's Windows virtual environment.")

    for file_name in ENGINE_FILES:
        _require(REPO_ROOT / file_name)
    rubberband = REPO_ROOT / "external" / "rubberband.exe"
    if not rubberband.is_file():
        rubberband = REPO_ROOT / "rubberband.exe"
    if require_rubberband and not rubberband.is_file():
        raise RuntimeError(
            "rubberband.exe is required for a self-contained release because OCTAVE_BOOST uses it. "
            "Place a redistributable Windows Rubber Band binary at external/rubberband.exe, then rerun this command."
        )

    _clear_previous_template(destination)

    python_destination = destination / "python"
    _copy_tree(base_python, python_destination, _ignore_runtime)
    _copy_tree(venv_site_packages, python_destination / "Lib" / "site-packages", _ignore_runtime)

    for file_name in ENGINE_FILES:
        shutil.copy2(REPO_ROOT / file_name, destination / file_name)
    if rubberband.is_file():
        shutil.copy2(rubberband, destination / rubberband.name)

    for directory_name in ENGINE_DIRECTORIES:
        _copy_tree(REPO_ROOT / directory_name, destination / directory_name, _ignore_engine)

    manifest = {
        "python": sys.version.split()[0],
        "ffmpeg_required_on_path": True,
        "rubberband": rubberband.is_file(),
        "runtime": "DECTALK Choir Studio",
    }
    (destination / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare the self-contained Choir Studio release runtime.")
    parser.add_argument("--output", type=Path, default=TEMPLATE_ROOT)
    parser.add_argument(
        "--allow-no-rubberband",
        action="store_true",
        help="Build without OCTAVE_BOOST support. The default enforces a fully self-contained audio runtime.",
    )
    args = parser.parse_args()
    build_template(args.output.resolve(), require_rubberband=not args.allow_no_rubberband)
    print(f"Prepared self-contained runtime template: {args.output.resolve()}")


if __name__ == "__main__":
    main()
