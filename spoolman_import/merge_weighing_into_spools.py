#!/usr/bin/env python3
"""
Merge weight data from weighing_sheet.csv into spools.csv by matching name.
Use after weighing spools: fill the sheet, then run this to update remaining_g
and empty_spool_g in spools.csv. Does not change other columns.
Python 3.11+.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

SPOOLMAN_DIR = Path(__file__).resolve().parent
WEIGHING_SHEET = SPOOLMAN_DIR / "weighing_sheet.csv"
SPOOLS_CSV = SPOOLMAN_DIR / "spools.csv"

SPOOLS_COLS = [
    "name",
    "brand",
    "material",
    "color",
    "status",
    "location",
    "remaining_g",
    "empty_spool_g",
    "notes",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Merge weighing_sheet.csv into spools.csv (update remaining_g, empty_spool_g by name)."
    )
    p.add_argument(
        "weighing",
        nargs="?",
        default=str(WEIGHING_SHEET),
        help="Path to weighing sheet CSV (default: weighing_sheet.csv)",
    )
    p.add_argument(
        "spools",
        nargs="?",
        default=str(SPOOLS_CSV),
        help="Path to spools CSV (default: spools.csv)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print updates without writing spools.csv.",
    )
    return p.parse_args()


def _read_weighing(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        return []
    # Prefer 'name'; accept legacy 'name_or_id'
    names_col = "name" if reader.fieldnames and "name" in reader.fieldnames else "name_or_id"
    return rows


def _read_spools(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return [], list(SPOOLS_COLS)
        fieldnames = list(reader.fieldnames)
        for col in SPOOLS_COLS:
            if col not in fieldnames:
                fieldnames.append(col)
        rows = []
        for row in reader:
            r = {k: (row.get(k) or "").strip() for k in fieldnames}
            rows.append(r)
    return rows, fieldnames


def _numeric(s: str) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        v = float(s)
        return str(int(v)) if v == int(v) else str(v)
    except ValueError:
        return None


def main() -> None:
    args = _parse_args()
    weighing_path = Path(args.weighing)
    spools_path = Path(args.spools)

    if not weighing_path.exists():
        print(f"Error: weighing sheet not found: {weighing_path}", file=sys.stderr)
        sys.exit(1)
    if not spools_path.exists():
        print(f"Error: spools CSV not found: {spools_path}", file=sys.stderr)
        sys.exit(1)

    weighing_rows = _read_weighing(weighing_path)
    spools_data = _read_spools(spools_path)
    if not spools_data:
        print("Error: spools.csv is empty or missing columns.", file=sys.stderr)
        sys.exit(1)
    spools_rows, fieldnames = spools_data

    # Build updates from weighing sheet: name -> {remaining_g?, empty_spool_g?}
    updates: dict[str, dict[str, str]] = {}
    for w in weighing_rows:
        name = (w.get("name") or w.get("name_or_id") or "").strip()
        if not name:
            continue
        remaining = _numeric(w.get("remaining_g") or "")
        current = _numeric(w.get("current_weight_g") or "")
        empty = _numeric(w.get("empty_spool_g") or "")
        if remaining is None and current and empty:
            try:
                remaining = str(int(float(current) - float(empty)))
            except (ValueError, TypeError):
                pass
        if remaining is not None or empty is not None:
            updates[name] = {}
            if remaining is not None:
                updates[name]["remaining_g"] = remaining
            if empty is not None:
                updates[name]["empty_spool_g"] = empty

    if not updates:
        print("No weight data to merge (fill name and remaining_g/empty_spool_g or current_weight_g+empty_spool_g in weighing sheet).")
        return

    # Apply to spools rows (match by name)
    updated_count = 0
    for row in spools_rows:
        name = (row.get("name") or "").strip()
        if not name or name not in updates:
            continue
        u = updates[name]
        changed = False
        if "remaining_g" in u and row.get("remaining_g") != u["remaining_g"]:
            row["remaining_g"] = u["remaining_g"]
            changed = True
        if "empty_spool_g" in u and row.get("empty_spool_g") != u["empty_spool_g"]:
            row["empty_spool_g"] = u["empty_spool_g"]
            changed = True
        if changed:
            updated_count += 1
            if args.dry_run:
                print(f"  Would update {name!r}: remaining_g={row.get('remaining_g')!r} empty_spool_g={row.get('empty_spool_g')!r}")

    if args.dry_run:
        print(f"Dry run: would update {updated_count} spool(s).")
        return

    with open(spools_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(spools_rows)

    print(f"Merged weights for {updated_count} spool(s) into {spools_path}. Run validate_spools.py then import if needed.")


if __name__ == "__main__":
    main()
