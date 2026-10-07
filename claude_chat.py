"""Compatibility entrypoint for existing Claude Chat launch commands."""

import runpy
from pathlib import Path


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("chatudex.py")), run_name="__main__")
