#!/usr/bin/env python3
"""Explicit focused test plan/run entry point; never invokes a full-suite wrapper."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.focused_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
