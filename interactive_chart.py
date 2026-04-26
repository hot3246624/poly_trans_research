#!/usr/bin/env python3
"""Legacy compatibility wrapper.

The legacy interactive chart script has moved to legacy/tools/interactive_chart.py.
"""

from __future__ import annotations

import runpy
from pathlib import Path

LEGACY = Path(__file__).resolve().parent / "legacy" / "tools" / "interactive_chart.py"

if __name__ == "__main__":
    if not LEGACY.exists():
        raise SystemExit(f"Missing legacy script: {LEGACY}")
    runpy.run_path(str(LEGACY), run_name="__main__")
