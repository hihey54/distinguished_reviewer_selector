"""Produce platform-specific release archives from PyInstaller on their native OS.

Usage: python .github/scripts/package_release.py windows|macos-intel|macos-arm64|linux
Run *after* the PyInstaller build on that platform. Standard library only.
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
APP = "PCRecognitionSelector"
DEST = ROOT / "release"


def main(platform: str):
    DEST.mkdir(exist_ok=True)
    source = ROOT / "dist" / APP
    if platform == "windows":
        if not (source / (APP + ".exe")).is_file():
            raise FileNotFoundError(f"Missing Windows executable in {source}")
        path = DEST / f"{APP}-windows-x64.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(ROOT / "LICENSE", arcname="LICENSE.txt")
            z.write(ROOT / "desktop_app" / "README.md", arcname="README.md")
            for f in sorted(source.rglob("*")):
                if f.is_file():
                    z.write(f, arcname=str(Path(APP) / f.relative_to(source)))
    elif platform.startswith("macos-"):
        bundle = ROOT / "dist" / (APP + ".app")
        if not (bundle / "Contents" / "MacOS" / APP).is_file():
            raise FileNotFoundError(f"Missing .app bundle at {bundle}")
        path = DEST / f"{APP}-{platform}.zip"
        # ditto preserves macOS app bundle permissions/symlinks/resource forks.
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        str(bundle), str(path)], check=True)
        with zipfile.ZipFile(path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(ROOT / "LICENSE", arcname="LICENSE.txt")
            z.write(ROOT / "desktop_app" / "README.md", arcname="README.md")
    elif platform == "linux":
        if not (source / APP).is_file():
            raise FileNotFoundError(f"Missing Linux binary in {source}")
        path = DEST / f"{APP}-linux-x64.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            tar.add(source, arcname=APP, recursive=True)
            tar.add(ROOT / "LICENSE", arcname=f"{APP}/LICENSE.txt")
            tar.add(ROOT / "desktop_app" / "README.md", arcname=f"{APP}/README.md")
    else:
        raise ValueError("Expected windows, macos-intel, macos-arm64, or linux")
    print("Created", path, f"({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: package_release.py windows|macos-intel|macos-arm64|linux")
    main(sys.argv[1])
