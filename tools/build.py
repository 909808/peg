#!/usr/bin/env python3
"""Build PEG into a single runnable file.

    python3 tools/build.py

Produces ``dist/peg.pyz``: one file, about 130 KiB, that anybody with Python
3.11 can run with ``python3 peg.pyz``. No install, no virtualenv, no
dependencies -- copy it onto a memory stick and it works.

This uses ``zipapp`` from the standard library. A .pyz is just a zip archive
with a ``__main__.py`` at its root, which Python knows how to execute
directly. The Earth dataset travels inside the archive and is read through
``importlib.resources``, which reads from zip imports as happily as from disk.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipapp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "peg.pyz")

#: Things that must never end up inside the archive.
JUNK = ("__pycache__", ".pyc", ".pyo", ".DS_Store")


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in JUNK or n.endswith(JUNK[1:])}


def main() -> int:
    src = os.path.join(ROOT, "peg")
    if not os.path.isdir(src):
        print(f"error: no peg package at {src}", file=sys.stderr)
        return 1
    data = os.path.join(src, "world", "earth.json.gz")
    if not os.path.exists(data):
        print("error: peg/world/earth.json.gz is missing.\n"
              "       Rebuild it with: python3 tools/bake_earth.py",
              file=sys.stderr)
        return 1

    os.makedirs(DIST, exist_ok=True)
    with tempfile.TemporaryDirectory() as staging:
        shutil.copytree(src, os.path.join(staging, "peg"), ignore=_ignore)
        zipapp.create_archive(
            staging,
            target=OUT,
            main="peg.__main__:main",
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
    os.chmod(OUT, 0o755)

    size = os.path.getsize(OUT)
    print(f"built {os.path.relpath(OUT, ROOT)}  ({size/1024:.0f} KiB)")
    print()
    print("  run it with:   python3 dist/peg.pyz")
    print("  or on Linux/macOS, just:   ./dist/peg.pyz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
