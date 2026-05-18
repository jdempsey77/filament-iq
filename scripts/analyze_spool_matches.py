#!/usr/bin/env python3
"""
analyze_spool_matches.py — Check 3dfilamentprofiles.com match confidence for all Spoolman spools.

Usage:
    python3 analyze_spool_matches.py

Requires:
    - Spoolman running at http://192.168.4.124:7912
    - filaments.json at ~/code/filament-profiles-data/filaments.json
    - filament_profiles.py copied to same directory as this script
"""

import json
import sys
import urllib.request
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────
SPOOLMAN_URL = "http://192.168.4.124:7912"
FILAMENTS_JSON = Path.home() / "code/filament-profiles-data/filaments.json"
SLIM_LABELS_DIR = Path.home() / "code/filament-profiles-data/slim_labels"

# ── Import FilamentProfilesClient ─────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
try:
    from filament_profiles import FilamentProfilesClient
except ImportError:
    # Try loading from filament-iq repo
    sys.path.insert(0, str(Path.home() / "code/filament-iq/apps/filament_iq"))
    from filament_profiles import FilamentProfilesClient


def fetch_spools():
    url = f"{SPOOLMAN_URL}/api/v1/spool?limit=1000"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print(f"Loading filaments from {FILAMENTS_JSON}...")
    client = FilamentProfilesClient(str(FILAMENTS_JSON))
    if not client.available:
        print("ERROR: FilamentProfilesClient failed to load. Check filaments.json path.")
        sys.exit(1)
    print(f"Loaded {len(client._index)} brands.\n")

    print("Fetching spools from Spoolman...")
    spools = fetch_spools()
    print(f"Found {len(spools)} spools.\n")

    results = []
    for spool in sorted(spools, key=lambda s: s["id"]):
        spool_id = spool["id"]
        filament = spool.get("filament") or {}
        vendor = str((filament.get("vendor") or {}).get("name") or "")
        material = str(filament.get("material") or "")
        name = str(filament.get("name") or "")
        color = str(filament.get("color_hex") or "")

        profile = client.lookup(vendor=vendor, material=material, filament_name=name)

        # Check if slim label PNG exists
        label_exists = (SLIM_LABELS_DIR / f"{profile.profile_id}.png").exists() if profile.profile_id else False

        results.append({
            "spool_id": spool_id,
            "vendor": vendor,
            "material": material,
            "name": name,
            "confidence": profile.confidence,
            "profile_id": profile.profile_id,
            "label_exists": label_exists,
        })

    # ── Summary ───────────────────────────────────────────────────────
    high = [r for r in results if r["confidence"] == "high"]
    medium = [r for r in results if r["confidence"] == "medium"]
    low = [r for r in results if r["confidence"] == "low"]
    none_ = [r for r in results if r["confidence"] == "none"]

    print(f"{'='*70}")
    print(f"MATCH SUMMARY: {len(spools)} spools")
    print(f"{'='*70}")
    print(f"  High confidence:   {len(high):3d} ({len(high)/len(spools)*100:.0f}%)")
    print(f"  Medium confidence: {len(medium):3d} ({len(medium)/len(spools)*100:.0f}%)")
    print(f"  Low confidence:    {len(low):3d} ({len(low)/len(spools)*100:.0f}%)")
    print(f"  No match:          {len(none_):3d} ({len(none_)/len(spools)*100:.0f}%)")
    print()

    # ── Detail by confidence ──────────────────────────────────────────
    for label, group in [("HIGH", high), ("MEDIUM", medium), ("LOW", low), ("NONE", none_)]:
        if not group:
            continue
        print(f"{'─'*70}")
        print(f"{label} CONFIDENCE ({len(group)} spools):")
        print(f"{'─'*70}")
        for r in group:
            label_str = "✓" if r["label_exists"] else "✗"
            pid = f"profile_id={r['profile_id']}" if r["profile_id"] else "no profile"
            print(f"  Spool {r['spool_id']:3d} [{label_str}] {pid:20s} {r['vendor']} - {r['name']}")
        print()

    # ── Actionable: no label PNG ──────────────────────────────────────
    missing_labels = [r for r in results if r["profile_id"] and not r["label_exists"]]
    if missing_labels:
        print(f"{'='*70}")
        print(f"WARNING: {len(missing_labels)} spools have a profile_id but MISSING slim label PNG:")
        for r in missing_labels:
            print(f"  Spool {r['spool_id']:3d} profile_id={r['profile_id']} {r['vendor']} - {r['name']}")
        print()

    no_match = [r for r in results if r["confidence"] in ("low", "none")]
    if no_match:
        print(f"{'='*70}")
        print(f"ACTION NEEDED: {len(no_match)} spools need manual profile_id assignment:")
        print(f"  Find them at https://3dfilamentprofiles.com and note the ID from the URL")
        print(f"  e.g. https://3dfilamentprofiles.com/filament/details/131 → profile_id=131")
        print()
        for r in no_match:
            print(f"  Spool {r['spool_id']:3d} conf={r['confidence']:6s} {r['vendor']} - {r['name']}")


if __name__ == "__main__":
    main()
