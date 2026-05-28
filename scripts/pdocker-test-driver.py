#!/usr/bin/env python3
"""Deprecated compatibility wrapper for scripts/skydnir-test-driver.py."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


TARGET_REL = "scripts/skydnir-test-driver.py"
TARGET = Path(__file__).resolve().parents[1] / TARGET_REL
sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")
