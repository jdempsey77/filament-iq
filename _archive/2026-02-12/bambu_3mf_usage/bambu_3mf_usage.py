#!/usr/bin/env python3
"""
CLI: Download 3MF from Bambu printer via FTPS, parse per-filament usage, map to AMS slots.
Outputs result.json with matches (slot, spool_id, used_g) or empty matches + notes on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure package dir is on path when run as script
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from ftps_client import (
    list_3mf_files,
    pick_best_3mf,
    download_3mf,
)
from parse_3mf import parse_3mf, FilamentUsage
from map_filaments import map_filaments_to_slots


def load_json_path(path: str) -> dict | list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Download 3MF from Bambu printer, parse filament usage, map to Spoolman slots."
    )
    ap.add_argument("--printer-ip", default=None, help="Printer IP (required when not using --local-3mf)")
    ap.add_argument("--access-code", default=None, help="Printer access code (required when not using --local-3mf)")
    ap.add_argument("--task-name", required=True, help="Task/job name (e.g. from HA task_name sensor)")
    ap.add_argument("--ams-json", required=True, help="Path to AMS state JSON: {slot: {color_hex, material}}")
    ap.add_argument("--spoolmap-json", required=True, help="Path to slot→spool_id JSON: {slot: spool_id}")
    ap.add_argument("--out", default="result.json", help="Output JSON path (default result.json)")
    ap.add_argument("--download-dir", default=None, help="Directory to download 3MF into (default temp)")
    ap.add_argument("--density", type=float, default=None, help="Filament density g/cm³ (to convert used_m to g)")
    ap.add_argument("--local-3mf", default=None, help="Skip FTPS; parse this local 3MF path (for testing)")
    args = ap.parse_args()

    if not args.local_3mf and (not args.printer_ip or not args.access_code):
        print("Error: --printer-ip and --access-code are required when not using --local-3mf.", file=sys.stderr)
        return 1

    result = {
        "matches": [],
        "downloaded_file": None,
        "notes": [],
    }

    # 1) Load AMS state and spool map
    try:
        ams_raw = load_json_path(args.ams_json)
        spool_raw = load_json_path(args.spoolmap_json)
    except FileNotFoundError as e:
        result["notes"].append(f"Input file not found: {e}")
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return 1
    except json.JSONDecodeError as e:
        result["notes"].append(f"Invalid JSON: {e}")
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return 1

    # Normalize to slot (int) -> value; spool_map only slots with valid numeric spool_id
    ams_state = {int(k): v for k, v in ams_raw.items() if str(k).isdigit() and 1 <= int(k) <= 6}
    spool_map = {}
    for k, v in spool_raw.items():
        if not (str(k).isdigit() and 1 <= int(k) <= 6):
            continue
        try:
            sid = int(v) if v not in (None, "", []) else None
        except (TypeError, ValueError):
            sid = None
        if sid is not None:
            spool_map[int(k)] = sid

    local_path = None
    remote_path_for_output = None
    if args.local_3mf:
        if os.path.isfile(args.local_3mf):
            local_path = args.local_3mf
            remote_path_for_output = args.local_3mf
            result["notes"].append("Using --local-3mf (skip FTPS)")
        else:
            result["notes"].append(f"--local-3mf file not found: {args.local_3mf}")
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            return 0
    else:
        # 2) List and pick 3MF from /cache/
        file_list, raw_filenames = list_3mf_files(args.printer_ip, args.access_code)
        remote_path, pick_note = pick_best_3mf(args.printer_ip, args.access_code, args.task_name, file_list)
        result["notes"].append(pick_note)
        if not remote_path:
            # Debug: distinguish "list failed" vs "no .3mf matched"
            if pick_note == "No 3MF files found on printer" and raw_filenames is not None:
                n = len(raw_filenames)
                if n == 0:
                    result["notes"].append("Listing returned 0 files (FTPS list may have failed).")
                else:
                    first = raw_filenames[:20]
                    result["notes"].append(f"Listing had {n} files (none .3mf); first 20: {first!r}")
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            return 0  # No 3MF found; matches stay empty
        remote_path_for_output = remote_path  # e.g. /cache/Job.3mf
        # 3) Download
        local_path, dl_note = download_3mf(
            args.printer_ip,
            args.access_code,
            remote_path,
            local_dir=args.download_dir,
        )
        result["notes"].append(dl_note)
        if not local_path:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            return 0

    result["downloaded_file"] = remote_path_for_output

    # 4) Parse 3MF (slice_info.config; optional filament_sequence.json for order)
    usages, parse_notes, filament_order = parse_3mf(local_path)
    result["notes"].extend(parse_notes)
    if not usages:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return 0

    # 5) Map to slots (color + material; order as tie-breaker)
    matches, map_notes = map_filaments_to_slots(
        usages,
        ams_state,
        spool_map,
        density_g_per_cm3=args.density,
        filament_order=filament_order,
    )
    result["notes"].extend(map_notes)
    result["matches"] = matches

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
