#!/usr/bin/env python3
"""
Spool detection harness: evaluate baseline vs after snapshot.
Usage: harness_spool_detection_eval.py <baseline.json> <after.json> <rfid|nonrfid>
Exit: 0 = PASS, 1 = FAIL. Prints concise summary and reasons.
"""

import json
import sys
from pathlib import Path
from typing import Optional


def load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_tray_attrs(snap: dict) -> dict:
    ha = snap.get("ha") or {}
    tray = ha.get("tray_entity_state") or {}
    if not isinstance(tray, dict):
        return {}
    # Capture shape: { status_code, content_type, body, json }; attributes in .json.attributes
    if tray.get("json") and isinstance(tray["json"], dict) and "attributes" in tray["json"]:
        return tray["json"]["attributes"]
    if "attributes" in tray:
        return tray["attributes"]
    return {}


def get_derived(snap: dict) -> dict:
    return snap.get("derived") or {}


def rfid_detected(snap: dict) -> bool:
    d = get_derived(snap)
    tag = d.get("tag_uid")
    tray_uuid = d.get("tray_uuid")
    if tag and str(tag).strip():
        return True
    if tray_uuid and str(tray_uuid).strip():
        return True
    attrs = get_tray_attrs(snap)
    tag_attr = attrs.get("tag_uid") or attrs.get("tag_uid_hex") or ""
    tray_attr = attrs.get("tray_uuid") or attrs.get("tray_id") or ""
    return bool(str(tag_attr).strip()) or bool(str(tray_attr).strip())


def tray_signature_set(snap: dict) -> bool:
    ha = snap.get("ha") or {}
    sig_state = ha.get("helper_tray_signature") or {}
    if not isinstance(sig_state, dict):
        return False
    # Capture shape: { status_code, content_type, body, json }; state in .json.state
    if sig_state.get("json") and isinstance(sig_state["json"], dict):
        val = sig_state["json"].get("state") or ""
        return bool(str(val).strip())
    val = sig_state.get("state") or ""
    return bool(str(val).strip())


def bound(snap: dict) -> bool:
    d = get_derived(snap)
    sid = d.get("helper_spool_id_int")
    if sid is None:
        return False
    try:
        return int(sid) > 0
    except (TypeError, ValueError):
        return False


def expected_location(snap: dict) -> str:
    return (get_derived(snap) or {}).get("expected_spoolman_location") or ""


def spoolman_by_helper(snap: dict) -> dict:
    return (snap.get("spoolman") or {}).get("by_helper_id") or {}


def spoolman_by_tag(snap: dict) -> dict:
    return (snap.get("spoolman") or {}).get("by_tag_uid") or {}


def spoolman_reflects_location(snap: dict) -> bool:
    by_helper = spoolman_by_helper(snap)
    if not isinstance(by_helper, dict):
        return False
    # Capture stores { status_code, content_type, body, json }; use .json when present, else try .body
    if by_helper.get("status_code") != 200:
        return False
    body = by_helper.get("json")
    if body is None:
        raw = by_helper.get("body")
        if raw is None:
            return False
        if isinstance(raw, str):
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                return False
        else:
            body = raw
    loc = (body or {}).get("location") or ""
    exp = expected_location(snap)
    return loc == exp


def matching_spool_from_tag(snap: dict) -> Optional[dict]:
    by_tag = spoolman_by_tag(snap)
    m = by_tag.get("matching_spool")
    if m is None:
        return None
    if isinstance(m, dict):
        return m
    return None


def matching_spool_location(snap: dict) -> str:
    m = matching_spool_from_tag(snap)
    if not m:
        return ""
    return (m.get("location") or "").strip()


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: harness_spool_detection_eval.py <baseline.json> <after.json> <rfid|nonrfid>", file=sys.stderr)
        return 1

    baseline_path, after_path, mode = sys.argv[1], sys.argv[2], sys.argv[3].lower()
    if mode not in ("rfid", "nonrfid"):
        print("Mode must be rfid or nonrfid", file=sys.stderr)
        return 1

    baseline = load(baseline_path)
    after = load(after_path)

    # Computed flags for AFTER
    rfid = rfid_detected(after)
    tray_sig = tray_signature_set(after)
    is_bound = bound(after)
    reflects = spoolman_reflects_location(after)
    match_by_tag = matching_spool_from_tag(after)
    match_loc = matching_spool_location(after)

    reasons = []
    passed = True

    if mode == "rfid":
        if not rfid:
            reasons.append("RFID not detected (no tag_uid/tray_uuid in after tray)")
            passed = False
        if not tray_sig:
            reasons.append("tray_signature not set in after (RFID mode requires it)")
            passed = False
        if is_bound:
            if not reflects:
                reasons.append("bound but Spoolman location does not match expected")
                passed = False
        else:
            if match_by_tag is not None:
                if match_loc != "New":
                    reasons.append("not bound but Spoolman match by tag_uid exists and location is not 'New'")
                    passed = False
        # Optional: if bound, we already required reflects; if not bound, we required null or New.

    else:  # nonrfid
        if rfid:
            reasons.append("non-RFID mode but tag_uid/tray_uuid detected in after")
            passed = False
        if is_bound:
            if not reflects:
                reasons.append("bound but Spoolman location does not match expected")
                passed = False

    # Summary
    print("--- Spool detection harness eval ---")
    print(f"Mode: {mode}")
    print(f"RFID detected: {rfid}")
    print(f"tray_signature set: {tray_sig}")
    print(f"bound (helper_spool_id_int>0): {is_bound}")
    print(f"spoolman_reflects_location: {reflects}")
    if mode == "rfid" and match_by_tag is not None:
        print(f"spoolman match by tag_uid: location={match_loc!r}")
    if reasons:
        print("Reasons:")
        for r in reasons:
            print(f"  - {r}")
    else:
        print("Reasons: (none)")
    print("------------------------------------")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
