"""Ingest the worker's published points snapshot into the Earn database.

Run from a systemd timer on the host, every five minutes. The worker computes;
the host only records the difference and the redemption state.
"""
from __future__ import annotations

import argparse
import sys

from . import db, domain
from .config import load


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default="")
    args = parser.parse_args()
    cfg = load()
    try:
        payload = domain.read_snapshot(args.snapshot or cfg.points_snapshot)
    except (OSError, ValueError) as exc:
        print(f"ingest: cannot read snapshot: {exc}", file=sys.stderr)
        return 2
    conn = db.connect(cfg.db_path)
    try:
        result = domain.ingest(conn, payload)
    finally:
        conn.close()
    print(
        f"ingest: {result['nodes']} nodes at {result['generated_at']}, "
        f"credited {result['credited']:,.1f} points"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
