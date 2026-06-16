from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
RELEASE_DIR = ROOT / "release"
DIST_DIR = BUILD_DIR / "dist"
PRODUCT_NAME = "DataAcquisition"


def require_files(paths: list[Path]) -> None:
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.exists()]
    if missing:
        raise SystemExit("Release files are missing: " + ", ".join(missing))


def run_pyinstaller() -> Path:
    application_dir = DIST_DIR / PRODUCT_NAME
    icon = ROOT / "resources" / "icon.ico"
    data_files = [
        (ROOT / "ui", "ui"),
        (ROOT / "resources", "resources"),
        (ROOT / "version.json", "."),
        (ROOT / "profile_database.h5", "."),
    ]
    require_files([ROOT / "main.py", icon, *(source for source, _ in data_files)])

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--contents-directory",
        ".",
        "--name",
        PRODUCT_NAME,
        "--icon",
        str(icon),
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR / "pyinstaller"),
        "--specpath",
        str(BUILD_DIR),
    ]
    for source, destination in data_files:
        command.extend(("--add-data", f"{source}{os.pathsep}{destination}"))
    command.append(str(ROOT / "main.py"))

    print(f"Building {PRODUCT_NAME}...")
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"PyInstaller failed with exit code {exc.returncode}.") from exc

    for document in ("README.md", "Installation_guide.txt"):
        source = ROOT / document
        if source.exists():
            shutil.copy2(source, application_dir / document)
    return application_dir


def create_release_archive(application_dir: Path) -> Path:
    archive_path = RELEASE_DIR / f"{PRODUCT_NAME}.zip"
    archive_path.unlink(missing_ok=True)
    shutil.make_archive(
        str(archive_path.with_suffix("")),
        "zip",
        root_dir=DIST_DIR,
        base_dir=PRODUCT_NAME,
    )
    shutil.rmtree(DIST_DIR)
    return archive_path


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "PyInstaller is required. Install the build dependencies with:\n"
            '  python -m pip install "pyinstaller>=6"',
            file=sys.stderr,
        )
        return 1

    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    release_application_dir = RELEASE_DIR / PRODUCT_NAME
    if release_application_dir.exists():
        shutil.rmtree(release_application_dir)
    BUILD_DIR.mkdir(exist_ok=True)
    RELEASE_DIR.mkdir(exist_ok=True)

    application_dir = run_pyinstaller()
    archive_path = create_release_archive(application_dir)

    print(f"\nRelease archive: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
