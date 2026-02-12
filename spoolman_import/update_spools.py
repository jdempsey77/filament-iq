#!/usr/bin/env python3
"""
Push remaining_g and empty_spool_g from spools.csv to Spoolman (PATCH existing spools).
Run after you edit weights in the CSV or run merge_weighing_into_spools.py. Requires spool_id
in the CSV (run export once to get spool_ids). Python 3.11+.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

import requests

SPOOLMAN_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = SPOOLMAN_DIR / "spools.csv"
DEFAULT_BASE_PATH = "/api/v1"
LOG = logging.getLogger("update_spools")


def _parse_args() -> argparse.Namespace:
    base_url = os.environ.get("SPOOLMAN_URL", "").rstrip("/")
    p = argparse.ArgumentParser(
        description="Push remaining_g and empty_spool_g from CSV to Spoolman (PATCH by spool_id)."
    )
    p.add_argument(
        "csv_file",
        nargs="?",
        default=str(DEFAULT_CSV),
        help="Path to spools CSV (default: spools.csv)",
    )
    p.add_argument(
        "--url",
        default=base_url,
        help="Spoolman base URL (e.g. http://host:7912). Overrides SPOOLMAN_URL.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would be updated, no API calls.")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return p.parse_args()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(levelname)s %(message)s",
        level=level,
        stream=sys.stderr,
    )


def main() -> None:
    args = _parse_args()
    _setup_logging(args.verbose)

    base_url = (args.url or os.environ.get("SPOOLMAN_URL") or "").strip().rstrip("/")
    if not base_url:
        print("Error: SPOOLMAN_URL or --url required.", file=sys.stderr)
        sys.exit(1)

    path = Path(args.csv_file)
    if not path.exists():
        print(f"Error: CSV not found: {path}", file=sys.stderr)
        sys.exit(1)

    rows: list[dict[str, str]] = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    updated = 0
    skipped = 0
    errors: list[str] = []

    for i, row in enumerate(rows, start=2):
        spool_id_s = (row.get("spool_id") or "").strip()
        if not spool_id_s:
            skipped += 1
            continue
        try:
            spool_id = int(spool_id_s)
        except ValueError:
            errors.append(f"Row {i}: invalid spool_id {spool_id_s!r}")
            continue

        remaining = (row.get("remaining_g") or "").strip()
        empty = (row.get("empty_spool_g") or "").strip()
        if not remaining and not empty:
            skipped += 1
            continue

        payload: dict[str, float] = {}
        if remaining:
            try:
                payload["remaining_weight"] = float(remaining)
            except ValueError:
                errors.append(f"Row {i} ({row.get('name', '')}): remaining_g not numeric {remaining!r}")
                continue
        if empty:
            try:
                payload["spool_weight"] = float(empty)
            except ValueError:
                errors.append(f"Row {i} ({row.get('name', '')}): empty_spool_g not numeric {empty!r}")
                continue

        if not payload:
            skipped += 1
            continue

        if args.dry_run:
            LOG.info("Would PATCH spool %s: %s", spool_id, payload)
            updated += 1
            continue

        url = f"{base_url}{DEFAULT_BASE_PATH}/spool/{spool_id}"
        try:
            r = requests.patch(url, json=payload, timeout=30)
            r.raise_for_status()
            updated += 1
            LOG.debug("Updated spool %s: %s", spool_id, payload)
        except requests.RequestException as e:
            errors.append(f"Row {i} (spool_id={spool_id}): {e}")

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    print(f"Updated {updated} spool(s) in Spoolman. Skipped {skipped} row(s) (no spool_id or no weights).")


if __name__ == "__main__":
    main()
