#!/usr/bin/env python3
"""Simple disk usage guard for raw/replay roots."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def dir_size_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for p in root.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                continue
    return total


def main() -> int:
    p = argparse.ArgumentParser(description="Check disk and dataset size budgets")
    p.add_argument("--path", action="append", required=True, help="Path to inspect (repeatable)")
    p.add_argument("--max-total-gb", type=float, default=20.0, help="Fail when summed path size exceeds this")
    p.add_argument("--min-disk-free-gb", type=float, default=10.0, help="Fail when free disk is below this")
    args = p.parse_args()

    paths = [Path(x).resolve() for x in args.path]
    total_bytes = sum(dir_size_bytes(x) for x in paths)
    total_gb = total_bytes / (1024 ** 3)

    disk = shutil.disk_usage(paths[0].anchor if paths else "/")
    free_gb = disk.free / (1024 ** 3)

    print(f"[disk_guard] inspected={','.join(str(p) for p in paths)}")
    print(f"[disk_guard] total_gb={total_gb:.3f} free_gb={free_gb:.3f}")

    if total_gb > float(args.max_total_gb):
        print(f"[disk_guard] FAIL total_gb>{args.max_total_gb}")
        return 2
    if free_gb < float(args.min_disk_free_gb):
        print(f"[disk_guard] FAIL free_gb<{args.min_disk_free_gb}")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
