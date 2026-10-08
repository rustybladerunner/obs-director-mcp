"""Build local wheel/sdist only after the public-content check passes. Never publish."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    subprocess.run([sys.executable, str(ROOT / "tools/public_check.py"), "--root", str(ROOT)], check=True)
    os.chdir(ROOT)
    from setuptools.build_meta import build_sdist, build_wheel
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    print("Wheel:", build_wheel(str(dist)))
    print("Source distribution:", build_sdist(str(dist)))


if __name__ == "__main__":
    main()
