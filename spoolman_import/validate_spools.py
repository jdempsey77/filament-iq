#!/usr/bin/env python3
"""
Validate spools.csv: required columns, numeric weights, no duplicate names, color hex.
Run before import or after editing the CSV. Python 3.11+.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

SPOOLMAN_DIR = Path(__file__).resolve().parent
SPOOLS_CSV = SPOOLMAN_DIR / "spools.csv"

REQUIRED_COLS = {"name", "brand", "material", "color", "status", "location", "remaining_g", "empty_spool_g", "notes"}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate spools.csv for import.")
    p.add_argument(
        "csv_file",
        nargs="?",
        default=str(SPOOLS_CSV),
        help="Path to spools CSV (default: spools.csv)",
    )
    return p.parse_args()


def _color_ok(value: str) -> bool:
    s = (value or "").strip().lstrip("#")
    if not s:
        return True
    return bool(re.match(r"^[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$", s))


def main() -> None:
    args = _parse_args()
    path = Path(args.csv_file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    errors: list[str] = []
    seen_names: set[str] = set()

    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        missing = REQUIRED_COLS - set(fieldnames)
        if missing:
            errors.append(f"Missing columns: {sorted(missing)}")
        for i, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            if not name:
                errors.append(f"Row {i}: missing name")
                continue
            if name in seen_names:
                errors.append(f"Row {i}: duplicate name {name!r}")
            seen_names.add(name)

            remaining = (row.get("remaining_g") or "").strip()
            if remaining:
                try:
                    v = float(remaining)
                    if v < 0:
                        errors.append(f"Row {i} ({name}): remaining_g must be >= 0")
                except ValueError:
                    errors.append(f"Row {i} ({name}): remaining_g not numeric {remaining!r}")

            empty = (row.get("empty_spool_g") or "").strip()
            if empty:
                try:
                    v = float(empty)
                    if v < 0:
                        errors.append(f"Row {i} ({name}): empty_spool_g must be >= 0")
                except ValueError:
                    errors.append(f"Row {i} ({name}): empty_spool_g not numeric {empty!r}")

            color = (row.get("color") or "").strip()
            if color and not _color_ok(color):
                errors.append(f"Row {i} ({name}): color should be 6- or 8-char hex (e.g. 0d0c0c or #0d0c0c), got {color!r}")

    if errors:
        print("Validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {len(seen_names)} spool(s), columns and values valid.")


if __name__ == "__main__":
    main()
