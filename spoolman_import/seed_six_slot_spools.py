#!/usr/bin/env python3
"""
Create exactly six spools in Spoolman in order so they receive IDs 1, 2, 3, 4, 5, 6.
Use after resetting Spoolman to zero spools. Requires one filament to exist (--filament-id).

Usage:
  SPOOLMAN_URL=http://host:7912 python3 seed_six_slot_spools.py --filament-id 1
  python3 seed_six_slot_spools.py --url http://host:7912 --filament-id 1

Python 3.11+.
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

DEFAULT_BASE_PATH = "/api/v1"

SLOT_LOCATIONS = [
    "AMS1_Slot1",
    "AMS1_Slot2",
    "AMS1_Slot3",
    "AMS1_Slot4",
    "AMS2_HT_Slot1",
    "AMS2_HT_Slot2",
]


def main() -> int:
    base_url = (os.environ.get("SPOOLMAN_URL") or "").rstrip("/")
    p = argparse.ArgumentParser(
        description="Create 6 spools in Spoolman in order (IDs 1-6 for AMS slots 1-6)."
    )
    p.add_argument(
        "--url",
        default=base_url,
        help="Spoolman base URL (e.g. http://host:7912). Overrides SPOOLMAN_URL.",
    )
    p.add_argument(
        "--filament-id",
        type=int,
        required=True,
        metavar="ID",
        help="Filament ID to use for all 6 spools (must exist in Spoolman).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without calling the API.",
    )
    args = p.parse_args()

    url_base = (args.url or "").rstrip("/")
    if not url_base:
        print("Error: SPOOLMAN_URL or --url required.", file=sys.stderr)
        return 1

    filament_id = args.filament_id
    if filament_id < 1:
        print("Error: --filament-id must be >= 1.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("Dry run: would create 6 spools with filament_id=%s" % filament_id)
        for i, loc in enumerate(SLOT_LOCATIONS, 1):
            print("  Slot %d -> location=%s (would get id=%d)" % (i, loc, i))
        print("\nSet in HA: ams_slot_1_spool_id=1 ... ams_slot_6_spool_id=6")
        return 0

    # Health check
    try:
        r = requests.get(url_base + "/api/v1/health", timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print("Spoolman unreachable: %s" % e, file=sys.stderr)
        return 1

    # Don't send remaining_weight unless the filament has "weight" set; Spoolman returns 400 otherwise.
    # Omitting weight means "assumed full"; HA will set remaining_weight via Assign & Update.
    created_ids: list[int] = []
    for i, location in enumerate(SLOT_LOCATIONS, 1):
        payload = {
            "filament_id": filament_id,
            "location": location,
            "archived": False,
        }
        post_url = url_base + DEFAULT_BASE_PATH + "/spool"
        try:
            r = requests.post(post_url, json=payload, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            print("Failed to create spool %d (%s): %s" % (i, location, e), file=sys.stderr)
            resp = getattr(e, "response", None)
            if resp is not None and getattr(resp, "text", None):
                print("Response: %s" % (resp.text[:500],), file=sys.stderr)
            return 1
        spool_id = r.json().get("id")
        if spool_id is None:
            print("Response missing id for spool %d" % i, file=sys.stderr)
            return 1
        created_ids.append(int(spool_id))
        print("Created spool id=%s location=%s" % (spool_id, location))

    print("")
    print("Created 6 spools with IDs: %s" % ", ".join(str(x) for x in created_ids))
    if created_ids == [1, 2, 3, 4, 5, 6]:
        print("IDs are 1-6 as expected. Set in Home Assistant:")
        print("  input_text.ams_slot_1_spool_id = \"1\" ... ams_slot_6_spool_id = \"6\"")
        print("(Developer Tools -> States, or ensure configuration.yaml initial values are used.)")
    else:
        print("Set in HA: ams_slot_1_spool_id=\"%s\" ams_slot_2_spool_id=\"%s\" ... ams_slot_6_spool_id=\"%s\""
              % (created_ids[0], created_ids[1], created_ids[5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
