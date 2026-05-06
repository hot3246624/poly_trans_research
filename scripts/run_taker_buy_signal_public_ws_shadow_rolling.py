#!/usr/bin/env python3
"""Rolling wrapper for taker BUY signal public WS shadow.

The base observer resolves `round-offsets` once at startup. This wrapper runs it
in short chunks so longer shadow sessions keep following fresh BTC 5m markets.
It still never places orders and never writes raw capture.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_CONFIG = Path("configs/xuan/taker_buy_signal_core_v1.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--prefix", default="btc-updown-5m")
    parser.add_argument("--round-offsets", default="0,1,2")
    parser.add_argument("--duration-sec", type=float, default=3600.0)
    parser.add_argument("--chunk-sec", type=float, default=840.0)
    parser.add_argument("--pause-sec", type=float, default=2.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trigger-source", choices=("last_trade_price", "price_change", "hybrid"), default="last_trade_price")
    parser.add_argument("--probe-immediate-pairs", default="0.99,1.00,1.01")
    parser.add_argument("--poll-sec", type=float, default=1.0)
    return parser.parse_args()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    args = parse_args()
    script = Path(__file__).with_name("run_taker_buy_signal_public_ws_shadow.py")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    meta_path = args.output_dir / "taker_buy_signal_public_ws_shadow_rolling_meta.jsonl"
    started = time.monotonic()
    chunk_idx = 0
    while True:
        elapsed = time.monotonic() - started
        remaining = args.duration_sec - elapsed
        if remaining <= 0:
            break
        chunk_idx += 1
        chunk_sec = min(args.chunk_sec, remaining)
        cmd = [
            sys.executable,
            str(script),
            "--config",
            str(args.config),
            "--prefix",
            args.prefix,
            "--round-offsets",
            args.round_offsets,
            "--duration-sec",
            str(round(chunk_sec, 3)),
            "--output-dir",
            str(args.output_dir),
            "--trigger-source",
            args.trigger_source,
            "--probe-immediate-pairs",
            args.probe_immediate_pairs,
            "--poll-sec",
            str(args.poll_sec),
        ]
        if chunk_idx > 1:
            cmd.append("--no-reset")
        with meta_path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "event": "chunk_start",
                        "chunk_idx": chunk_idx,
                        "ts": iso_now(),
                        "duration_sec": chunk_sec,
                        "cmd": cmd,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        proc = subprocess.run(cmd, check=False)
        with meta_path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "event": "chunk_end",
                        "chunk_idx": chunk_idx,
                        "ts": iso_now(),
                        "returncode": proc.returncode,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        if proc.returncode != 0:
            return proc.returncode
        if args.pause_sec > 0:
            time.sleep(min(args.pause_sec, max(0.0, args.duration_sec - (time.monotonic() - started))))
    print(json.dumps({"output_dir": str(args.output_dir), "chunks": chunk_idx}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
