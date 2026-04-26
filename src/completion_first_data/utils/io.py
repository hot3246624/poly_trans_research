"""I/O helpers for gzip jsonl files and safe path handling."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Dict, Iterable, Iterator, Any


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_jsonl_gz(path: Path, record: Dict[str, Any]) -> None:
    ensure_parent(path)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with gzip.open(path, "at", encoding="utf-8") as fp:
        fp.write(line)
        fp.write("\n")


def iter_jsonl_gz(path: Path) -> Iterator[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def glob_jsonl_gz(paths: Iterable[Path]) -> Iterator[Path]:
    for root in paths:
        if not root.exists():
            continue
        for file_path in sorted(root.rglob("*.jsonl.gz")):
            if file_path.is_file():
                yield file_path
