#!/usr/bin/env python3
"""
Set initial_weight to a default (e.g. 1000g) for every Spoolman spool that has
initial_weight empty (null) or zero. Uses Spoolman REST API: GET /spool, PATCH /spool/{id}.
Python 3.11+. Same SPOOLMAN_URL / --url as other spoolman_import scripts.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import requests

DEFAULT_BASE_PATH = "/api/v1"
DEFAULT_WEIGHT = 1000.0
LOG = logging.getLogger("set_empty_initial_weight")


def _parse_args() -> argparse.Namespace:
    base_url = os.environ.get("SPOOLMAN_URL", "").rstrip("/")
    p = argparse.ArgumentParser(
        description="Set initial_weight for spools that have it empty or zero (default 1000g)."
    )
    p.add_argument(
        "--url",
        default=base_url,
        help="Spoolman base URL (e.g. http://host:7912). Overrides SPOOLMAN_URL.",
    )
    p.add_argument(
        "--set",
        dest="weight",
        type=float,
        default=DEFAULT_WEIGHT,
        metavar="GRAMS",
        help=f"Value to set for initial_weight in grams (default: {DEFAULT_WEIGHT}).",
    )
    p.add_argument(
        "--allow-archived",
        action="store_true",
        help="Include archived spools.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would be updated, no PATCH calls.")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return p.parse_args()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(levelname)s %(message)s",
        level=level,
        stream=sys.stderr,
    )


def _is_empty_initial_weight(val) -> bool:
    """True if initial_weight is considered empty (null, missing, or 0)."""
    if val is None:
        return True
    try:
        return float(val) == 0
    except (TypeError, ValueError):
        return True


def main() -> None:
    args = _parse_args()
    _setup_logging(args.verbose)

    base_url = (args.url or os.environ.get("SPOOLMAN_URL") or "").strip().rstrip("/")
    if not base_url:
        print("Error: SPOOLMAN_URL or --url required.", file=sys.stderr)
        sys.exit(1)

    if args.weight <= 0:
        print("Error: --set must be > 0.", file=sys.stderr)
        sys.exit(1)

    list_url = f"{base_url}{DEFAULT_BASE_PATH}/spool"
    params = {"allow_archived": "true"} if args.allow_archived else {}
    try:
        r = requests.get(list_url, params=params, timeout=30)
        r.raise_for_status()
        spools = r.json()
    except requests.RequestException as e:
        print(f"Error: GET spool failed: {e}", file=sys.stderr)
        sys.exit(1)

    updated = 0
    skipped = 0
    errors: list[str] = []

    for s in spools:
        spool_id = s.get("id")
        if spool_id is None:
            continue
        initial = s.get("initial_weight")
        if not _is_empty_initial_weight(initial):
            skipped += 1
            LOG.debug("Spool %s: initial_weight=%s, skip", spool_id, initial)
            continue

        name = (s.get("filament") or {}).get("name", "")
        if args.dry_run:
            LOG.info("Would set spool id=%s (%s) initial_weight=%s", spool_id, name or "?", args.weight)
            updated += 1
            continue

        patch_url = f"{base_url}{DEFAULT_BASE_PATH}/spool/{spool_id}"
        try:
            resp = requests.patch(patch_url, json={"initial_weight": args.weight}, timeout=30)
            resp.raise_for_status()
            updated += 1
            LOG.info("Set spool id=%s (%s) initial_weight=%s", spool_id, name or "?", args.weight)
        except requests.RequestException as e:
            errors.append(f"Spool id={spool_id}: {e}")

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    print(f"Spools with empty/zero initial_weight set to {args.weight}g: {updated}. Skipped (already set): {skipped}.")


if __name__ == "__main__":
    main()
