#!/usr/bin/env python3
"""
Export all spools from Spoolman to spools.csv so the repo stays in sync with Spoolman.
Run after you add/edit/delete spools in the Spoolman UI. Then commit the updated CSV.
Python 3.11+. Same SPOOLMAN_URL / --url as import_spools.py.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

import requests

DEFAULT_CSV = "spools.csv"
DEFAULT_BASE_PATH = "/api/v1"
LOG = logging.getLogger("export_spools")

CSV_COLS = [
    "name",
    "brand",
    "material",
    "color",
    "status",
    "location",
    "remaining_g",
    "empty_spool_g",
    "notes",
    "spool_id",
]


def _parse_args() -> argparse.Namespace:
    base_url = os.environ.get("SPOOLMAN_URL", "").rstrip("/")
    p = argparse.ArgumentParser(
        description="Export all spools from Spoolman to CSV (keep repo in sync with Spoolman)."
    )
    p.add_argument(
        "-o",
        "--output",
        default=DEFAULT_CSV,
        help=f"Output CSV path (default: {DEFAULT_CSV})",
    )
    p.add_argument(
        "--url",
        default=base_url,
        help="Spoolman base URL (e.g. http://host:7912). Overrides SPOOLMAN_URL.",
    )
    p.add_argument(
        "--allow-archived",
        action="store_true",
        help="Include archived spools in the export.",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return p.parse_args()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(levelname)s %(message)s",
        level=level,
        stream=sys.stderr,
    )


def _get_spools(base_url: str, allow_archived: bool) -> list[dict]:
    url = f"{base_url}{DEFAULT_BASE_PATH}/spool"
    params = {"allow_archived": "true"} if allow_archived else {}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _spool_to_row(s: dict) -> dict[str, str]:
    filament = s.get("filament") or {}
    vendor = filament.get("vendor") or {}
    loc = (s.get("location") or "").strip()
    name = (filament.get("name") or "Unknown").strip()
    remaining = s.get("remaining_weight")
    spool_weight = s.get("spool_weight")
    return {
        "name": name,
        "brand": (vendor.get("name") or "").strip(),
        "material": (filament.get("material") or "").strip(),
        "color": (filament.get("color_hex") or "").strip(),
        "status": "archived" if s.get("archived") else "active",
        "location": loc,
        "remaining_g": str(int(remaining)) if remaining is not None else "",
        "empty_spool_g": str(int(spool_weight)) if spool_weight is not None else "",
        "notes": (s.get("comment") or "").strip(),
        "spool_id": str(s.get("id", "")),
    }


def main() -> None:
    args = _parse_args()
    _setup_logging(args.verbose)

    base_url = (args.url or os.environ.get("SPOOLMAN_URL") or "").strip().rstrip("/")
    if not base_url:
        print("Error: Spoolman URL is required. Set SPOOLMAN_URL or use --url.", file=sys.stderr)
        sys.exit(1)

    LOG.info("Fetching spools from %s", base_url)
    try:
        spools = _get_spools(base_url, args.allow_archived)
    except requests.RequestException as e:
        print(f"Error: failed to fetch spools: {e}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # If output exists, preserve remaining_g/empty_spool_g from CSV (so local edits aren't lost)
    existing_by_name: dict[str, dict[str, str]] = {}
    if out_path.exists():
        with open(out_path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("name") or "").strip()
                if name and (row.get("remaining_g") or row.get("empty_spool_g")):
                    existing_by_name[name] = row

    rows = []
    for s in spools:
        row = _spool_to_row(s)
        name = row["name"]
        if name in existing_by_name:
            for key in ("remaining_g", "empty_spool_g"):
                if existing_by_name[name].get(key):
                    row[key] = (existing_by_name[name][key] or "").strip()
        rows.append(row)

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Exported {len(rows)} spools to {out_path}. Commit the file to keep the repo in sync.")


if __name__ == "__main__":
    main()
