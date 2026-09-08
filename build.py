#!/usr/bin/env python3
"""Build a one-click, dependency-free app for the OS you run this on.

    pip install pyinstaller
    python build.py

Output lands in dist/. PyInstaller cannot cross-compile: run this on Windows to
get the .exe, on macOS to get the .app, on Linux to get the binary. To get all
three without owning all three machines, push to GitHub and let
.github/workflows/build.yml do it.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
NAME = "ClaudeSessionManager"


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is missing. Install it with:  pip install pyinstaller")

    args = [sys.executable, "-m", "PyInstaller", "--onefile", "--clean", "--noconfirm",
            "--name", NAME, str(HERE / "app.py")]

    if os.name == "nt":
        args.insert(-1, "--noconsole")          # double-click opens the browser, no console
        out = f"dist/{NAME}.exe"
    elif sys.platform == "darwin":
        args.insert(-1, "--windowed")           # produces a .app bundle
        out = f"dist/{NAME}.app"
    else:
        out = f"dist/{NAME}"

    icon = HERE / ("icon.ico" if os.name == "nt" else "icon.icns")
    if icon.exists():
        args[-1:-1] = ["--icon", str(icon)]

    print("building:", " ".join(args))
    subprocess.check_call(args, cwd=HERE)

    if sys.platform == "darwin":
        # Zipping preserves the bundle's executable bit; a raw .app in a download
        # loses it and macOS then refuses to open the app.
        shutil.make_archive(str(HERE / "dist" / NAME), "zip", HERE / "dist", f"{NAME}.app")
        print(f"\nBuilt {out} and dist/{NAME}.zip (send the .zip)")
    else:
        print(f"\nBuilt {out}")
        if os.name != "nt":
            os.chmod(HERE / "dist" / NAME, 0o755)


if __name__ == "__main__":
    main()
