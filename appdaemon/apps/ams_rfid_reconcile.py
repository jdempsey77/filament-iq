"""
AMS RFID Reconcile — Flow A (deterministic metadata) and Flow B (HA_SIG comment).

Flow A — Direct RFID tag mapping:
  Spool has no rfid_tag_uid, location Shelf, vendor Bambu Lab, material/color match tray.
  If exactly 1 metadata match → bind. If >1 → tiebreak (next-man-up, full-pick) or CONFLICT.

Flow B — HA_SIG comment fallback:
  Spool has comment == HA_SIG (exact), extra.ha_spool_uuid set, extra.rfid_tag_uid empty,
  location NOT AMS. If exactly 1 match → bind. If 0 or >1 → UNBOUND (fail closed).

JSON-string extra storage constraint:
  Spoolman stores extra.rfid_tag_uid and extra.ha_spool_uuid as JSON-encoded strings (e.g. "\\"ABC\\"").
  All reads decode then canonicalize; all writes use _patch_spool_extra_robust() with single encode (encode_extra_json_string).
  Never compare raw .extra.rfid_tag_uid directly.

Fail-closed behavior:
  Ambiguity (0 or >1 candidates) → UNBOUND. No new extra.* fields. Guard policy unchanged.

To stamp HA_SIG: PATCH spool comment to HA_SIG=bambu|filament_id=<id>|type=<type>|color_hex=<hex> (lowercase).
Example: HA_SIG=bambu|filament_id=gfa00|type=pla|color_hex=c12e1f
Convergence: _converge_ha_sig runs for every RESOLVED_UNIQUE slot (when not status_only); uses tray_meta only (no input_text helpers). Idempotent: one PATCH only when comment != ha_sig.
"""

import datetime
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import hassapi as hass

# Shared canonicalizer for Spoolman extra fields (prevent double-quoted rfid_tag_uid / ha_spool_uuid).
_scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
if _scripts_dir.is_dir():
    sys.path.insert(0, str(_scripts_dir))
try:
    from spoolman_extra_canonicalizer import (
        canonicalize_extra_scalar as _canonicalize_extra_scalar,
        canonicalize_ha_spool_uuid as _canon_ha_uuid,
        canonicalize_rfid_tag_uid as _canon_rfid,
        encode_extra_json_string as _encode_extra_json,
        is_double_encoded as _is_double_encoded,
        validate_extra_value_no_quotes,
    )
except ImportError as _import_err:
    import logging as _logging
    _logging.getLogger("ams_rfid_reconcile").error(
        "FATAL: spoolman_extra_canonicalizer import failed — reconciler cannot start safely. "
        "Ensure scripts/spoolman_extra_canonicalizer.py exists. Error: %s", _import_err,
    )
    raise RuntimeError(
        f"spoolman_extra_canonicalizer is required but failed to import: {_import_err}"
    ) from _import_err


# AMS slots 1–4 (AMS1), 5–6 (AMS_128 / AMS_129 HT). All have tray hardware; reconcile and location writes for all.
PHYSICAL_AMS_SLOTS = (1, 2, 3, 4, 5, 6)

TRAY_ENTITY_BY_SLOT = {
    1: "sensor.p1s_01p00c5a3101668_ams_1_tray_1",
    2: "sensor.p1s_01p00c5a3101668_ams_1_tray_2",
    3: "sensor.p1s_01p00c5a3101668_ams_1_tray_3",
    4: "sensor.p1s_01p00c5a3101668_ams_1_tray_4",
    5: "sensor.p1s_01p00c5a3101668_ams_128_tray_1",
    6: "sensor.p1s_01p00c5a3101668_ams_129_tray_1",
}

CANONICAL_LOCATION_BY_SLOT = {
    1: "AMS1_Slot1",
    2: "AMS1_Slot2",
    3: "AMS1_Slot3",
    4: "AMS1_Slot4",
    5: "AMS128_Slot1",
    6: "AMS129_Slot1",
}

# Location used when clearing a spool from an AMS slot (one spool per location invariant).
LOCATION_NOT_IN_AMS = "Shelf"
# End-of-life: spool at 0g and removed from AMS; excluded from matching.
LOCATION_EMPTY = "Empty"

# Deprecated/legacy location strings → never write. Map to Shelf so legacy cannot be written.
DEPRECATED_LOCATION_TO_CANONICAL = {
    "AMS2_HT_Slot1": "AMS128_Slot1",
    "AMS2_HT_Slot2": "AMS129_Slot1",
}
# Regex: any location matching these is forced to Shelf at PATCH boundary (slots 5/6 not physical).
LEGACY_LOCATION_PATTERN = re.compile(r"AMS2_HT_|HT1|HT2", re.IGNORECASE)

# Next-man-up tie-break ladder (multiple metadata-matched spools, UID lookup returned 0):
# 0) Strict mode: if strict_mode=True and len(candidates) > 1 → no auto-pick (caller must require explicit spool_id).
# 1) Prefer used: exactly one candidate has remaining_weight < initial_weight (e.g. 842 < 1000); others at or near full → pick that one.
# 2) Next man up: if lowest remaining_weight is clearly lower (margin >= 200g), choose it.
# 3) Full pick: if lowest and second are both >= 950g, choose smallest spool_id.
# 4) Otherwise: CONFLICT.
NEXT_MAN_MIN_MARGIN_G = 200  # Only choose lowest when clearly lower than next
FULL_SPOOL_G = 950           # Treat as "full" for deterministic full-vs-full pick

# Terminal slot statuses (stringly-typed in HA; use constants for comparisons).
STATUS_OK = "OK"
STATUS_OK_FIXED_EXPECTED = "OK: FIXED_EXPECTED"
STATUS_MISMATCH = "CONFLICT: MISMATCH"
STATUS_UNBOUND_NO_TAG = "UNBOUND: no_tag"
STATUS_UNBOUND_TRAY_UNAVAILABLE = "UNBOUND: TRAY_UNAVAILABLE"
STATUS_UNBOUND_MANUAL_CREATE = "UNBOUND: manual_create_required"
STATUS_UNBOUND_ACTION_REQUIRED = "UNBOUND: ACTION_REQUIRED"
STATUS_UNBOUND_FLOW_B_PARTIAL = "UNBOUND: FLOW_B_PARTIAL"
STATUS_CONFLICT_DUPLICATE_UID = "CONFLICT: DUPLICATE_UID"
STATUS_CONFLICT_MISSING_CANONICAL = "CONFLICT: missing_canonical_location"
STATUS_CONFLICT_AMBIGUOUS_METADATA = "CONFLICT: AMBIGUOUS_METADATA_NO_UNREGISTERED"
STATUS_PENDING_RFID_READ = "PENDING_RFID_READ"
STATUS_NON_RFID_REGISTERED = "NON_RFID_REGISTERED"
STATUS_UNIQUELY_RESOLVED = frozenset({STATUS_OK, STATUS_MISMATCH, STATUS_NON_RFID_REGISTERED})

# Seconds to wait after tray change before treating missing tag_uid as non-RFID
RFID_PENDING_SECONDS = 20  # fid>0 and status in this set → may converge HA_SIG

# Unbound reason codes (actionable classification for UNBOUND slots).
UNBOUND_TRAY_EMPTY = "UNBOUND_TRAY_EMPTY"
UNBOUND_NO_TAG_UID = "UNBOUND_NO_TAG_UID"
UNBOUND_NO_RFID_TAG_ALL_ZERO = "UNBOUND_NO_RFID_TAG_ALL_ZERO"
UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW = "UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW"
UNBOUND_TAG_UID_NO_MATCH = "UNBOUND_TAG_UID_NO_MATCH"
UNBOUND_TAG_UID_AMBIGUOUS = "UNBOUND_TAG_UID_AMBIGUOUS"
UNBOUND_TRAY_UNAVAILABLE = "UNBOUND_TRAY_UNAVAILABLE"
UNBOUND_ERROR = "UNBOUND_ERROR"
UNBOUND_SELECTED_UID_MISMATCH = "UNBOUND_SELECTED_UID_MISMATCH"
UNBOUND_HELPER_SPOOL_NOT_FOUND = "UNBOUND_HELPER_SPOOL_NOT_FOUND"
UNBOUND_SPOOLMAN_LOOKUP_FAILED = "UNBOUND_SPOOLMAN_LOOKUP_FAILED"
UNBOUND_HELPER_RFID_MISMATCH = "UNBOUND_HELPER_RFID_MISMATCH"
UNBOUND_HELPER_MATERIAL_MISMATCH = "UNBOUND_HELPER_MATERIAL_MISMATCH"

# HT non-RFID fingerprint statuses and reasons
STATUS_WAITING_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
STATUS_NEEDS_MANUAL_BIND = "NEEDS_MANUAL_BIND"
STATUS_LOW_CONFIDENCE = "LOW_CONFIDENCE_NO_AUTO_MATCH"
STATUS_OK_NONRFID = "OK_NON_RFID_REGISTERED"
UNBOUND_NONRFID_NO_MATCH = "NONRFID_NO_MATCH_CONFIDENT"
UNBOUND_LOW_CONFIDENCE = "LOW_CONFIDENCE_GENERIC_TRAY"
NONRFID_CONFIRM_SECONDS = 30

# RFID identity-stuck detection
STATUS_RFID_IDENTITY_STUCK = "RFID_IDENTITY_STUCK"
UNBOUND_RFID_NOT_REFRESHED = "RFID_NOT_REFRESHED_TRY_UNLOAD_LOAD"
RFID_STUCK_SECONDS = 60


def _normalize_rfid_tag_uid(val) -> str:
    """Normalize RFID tag UID for comparison. Handles Spoolman JSON-encoded string literal (e.g. '\\\"071F87ED00000100\\\"')."""
    if val is None:
        return ""
    s = str(val).strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            s = json.loads(s)
        except Exception:
            pass
    return str(s).strip().strip('"').strip().upper()


def _vendor_name(spool: dict) -> str:
    filament = spool.get("filament") or {}
    vendor = (filament.get("vendor") or {}).get("name") or ""
    return str(vendor).strip()


def _is_bambu_vendor(spool: dict) -> bool:
    return _vendor_name(spool).lower() == "bambu lab"


def _classify_unbound_reason(tray_meta, tag_uid, candidate_ids, ineligible_new_count, tray_empty=False, tray_state_str="", raw_tag_uid=None):
    """
    Classify why a slot is UNBOUND. Returns (reason, detail) for logging and transcripts.
    tray_empty: True if tray state is empty; tray_state_str used for Empty/unknown/unavailable.
    raw_tag_uid: raw tray attribute (to distinguish all-zero from blank when canonicalizer returns "").
    """
    if tray_empty or (str(tray_state_str or "").strip().lower() == "empty"):
        return (UNBOUND_TRAY_EMPTY, "tray_empty")
    if str(tray_state_str or "").strip().lower() in ("unknown", "unavailable", ""):
        return (UNBOUND_TRAY_UNAVAILABLE, "tray_unavailable")
    raw_norm = str(raw_tag_uid or "").strip().replace(" ", "").replace('"', "").lower()
    if raw_norm == "0000000000000000":
        return (UNBOUND_NO_RFID_TAG_ALL_ZERO, "non_rfid_tray")
    tag = str(tag_uid or "").strip().replace(" ", "").replace('"', "").lower()
    if not tag:
        return (UNBOUND_NO_TAG_UID, "tag_uid_blank")
    eligible_count = len(candidate_ids) if candidate_ids else 0
    if eligible_count == 0 and (ineligible_new_count or 0) > 0:
        return (UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW, f"eligible=0 ineligible_new={ineligible_new_count or 0}")
    if eligible_count == 0:
        return (UNBOUND_TAG_UID_NO_MATCH, f"eligible=0 ineligible_new={ineligible_new_count or 0}")
    if eligible_count > 1:
        return (UNBOUND_TAG_UID_AMBIGUOUS, f"eligible={eligible_count}")
    return (UNBOUND_ERROR, f"eligible={eligible_count}")


def _is_uniquely_resolved(status: str, final_spool_id: int) -> bool:
    """True when slot has a unique spool and terminal status is OK or MISMATCH (converge-eligible)."""
    return (final_spool_id or 0) > 0 and status in STATUS_UNIQUELY_RESOLVED


def _should_converge_ha_sig(status_only: bool, status: str, final_spool_id: int) -> bool:
    """True when we should run central HA signature convergence (not status_only and uniquely resolved)."""
    return not status_only and _is_uniquely_resolved(status, final_spool_id)


# Color tolerance: reduce false CONFLICT: MISMATCH when tray and spool hex are visually similar.
COLOR_DISTANCE_THRESHOLD = 90  # Euclidean RGB distance; <= this → not a color mismatch.

_HEX_6_RE = re.compile(r"^[0-9a-fA-F]{6}$")


def _normalize_hex_color(s: str):
    """
    Normalize color hex to 6-char lowercase RRGGBB, or None if invalid.
    Accepts #RRGGBB, RRGGBB, AARRGGBB (first 2 = alpha), RRGGBBAA (last 2 = alpha).
    For 8-hex: if first two chars are ff/00 assume AARRGGBB and use last 6; else assume RRGGBBAA and use first 6.
    """
    if s is None:
        return None
    raw = str(s).strip().lower().replace("#", "")
    if not raw:
        return None
    if len(raw) == 6 and _HEX_6_RE.match(raw):
        return raw
    if len(raw) == 8 and re.match(r"^[0-9a-f]{8}$", raw):
        if raw[:2] in ("ff", "00"):
            return raw[2:8]  # AARRGGBB → RRGGBB
        return raw[:6]  # RRGGBBAA → RRGGBB
    return None


def _hex_to_rgb(hex6: str):
    """Convert 6-char hex to (r, g, b) ints 0–255. Caller must pass valid hex6."""
    return (
        int(hex6[0:2], 16),
        int(hex6[2:4], 16),
        int(hex6[4:6], 16),
    )


def _rgb_distance(a_rgb, b_rgb) -> float:
    """Euclidean distance in RGB space; max ~441."""
    return math.sqrt(
        (a_rgb[0] - b_rgb[0]) ** 2
        + (a_rgb[1] - b_rgb[1]) ** 2
        + (a_rgb[2] - b_rgb[2]) ** 2
    )


def _colors_close(tray_hex: str, spool_hex: str, threshold: float = COLOR_DISTANCE_THRESHOLD):
    """
    Return (close: bool, distance: float, threshold: float).
    If either hex normalizes to None, returns (False, -1.0, threshold) so caller keeps strict behavior.
    """
    t = _normalize_hex_color(tray_hex)
    p = _normalize_hex_color(spool_hex)
    if t is None or p is None:
        return (False, -1.0, threshold)
    dist = _rgb_distance(_hex_to_rgb(t), _hex_to_rgb(p))
    return (dist <= threshold, dist, threshold)


def _safe_float(val, default):
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def tiebreak_choose_spool(candidate_spool_dicts, strict_mode=False):
    """
    Deterministic tie-break when multiple spools match (e.g. same color/filament).
    candidate_spool_dicts: list of spool dicts with at least id, remaining_weight; optional initial_weight, location, archived, comment, extra.
    Returns (chosen_spool_id, reason_string) or (None, reason_string).
    Reason strings: prefer_used, next_man_up, full_pick, STRICT_MODE_MULTIPLE_CANDIDATES, AMBIGUOUS_METADATA_NO_UNREGISTERED.
    """
    if not candidate_spool_dicts:
        return None, "AMBIGUOUS_METADATA_NO_UNREGISTERED"
    if len(candidate_spool_dicts) == 1:
        sid = int(candidate_spool_dicts[0].get("id") or 0)
        return (sid, "single_candidate") if sid > 0 else (None, "AMBIGUOUS_METADATA_NO_UNREGISTERED")
    if strict_mode:
        return None, "STRICT_MODE_MULTIPLE_CANDIDATES"

    # Build (id, remaining_g, initial_g); skip invalid
    rows = []
    for s in candidate_spool_dicts:
        sid = int(s.get("id") or 0)
        if sid <= 0:
            continue
        rem = _safe_float(s.get("remaining_weight"), -1.0)
        if rem <= 0:
            continue
        init = _safe_float(s.get("initial_weight"), 0.0)
        if init <= 0:
            init = rem  # treat as unknown initial
        rows.append((sid, rem, init))
    if len(rows) < 2:
        return (rows[0][0], "single_after_filter") if len(rows) == 1 else (None, "AMBIGUOUS_METADATA_NO_UNREGISTERED")

    rows.sort(key=lambda x: (x[1], x[0]))
    lowest_id, lowest_g, lowest_init = rows[0]
    second_id, second_g, second_init = rows[1]
    margin_g = second_g - lowest_g

    # 1) Prefer used: exactly one has remaining < initial (used spool); all others at or near full
    used_candidates = [(sid, rem, init) for sid, rem, init in rows if init > 0 and rem < init]
    if len(used_candidates) == 1 and len(rows) >= 2:
        only_used_id, only_used_rem, only_used_init = used_candidates[0]
        # Every other row (excluding the used one) must be effectively full
        others_full = all(
            rem >= FULL_SPOOL_G or (init > 0 and rem >= init)
            for sid, rem, init in rows
            if (sid, rem, init) != (only_used_id, only_used_rem, only_used_init)
        )
        if others_full:
            return only_used_id, "prefer_used"

    # 2) Next man up
    if margin_g >= NEXT_MAN_MIN_MARGIN_G:
        return lowest_id, "next_man_up"
    # 3) Full pick
    if lowest_g >= FULL_SPOOL_G and second_g >= FULL_SPOOL_G:
        return min(sid for sid, g, _ in rows if g >= FULL_SPOOL_G), "full_pick"
    return None, "AMBIGUOUS_METADATA_NO_UNREGISTERED"

# Tray color hex values that are not authoritative (sensor default/empty); skip color comparison and COLOR_WARNING.
TRAY_HEX_NON_AUTHORITATIVE = frozenset({"", "000000", "00000000", "none", "unknown"})
TRAY_HEX_VALID_PATTERN = re.compile(r"^[0-9a-f]{6}$")


class AmsRfidReconcile(hass.Hass):
    def initialize(self):
        self.log("ams_rfid_reconcile VERSION=2026-02-18 flow-b-ha-sig", level="INFO")
        self.log("spoolman_extra_canonicalizer loaded OK", level="INFO")
        self.enabled = bool(self.args.get("enabled", True))
        if not self.enabled:
            self.log("AMS RFID reconcile disabled by config (enabled=false).")
            return

        self.spoolman_url = str(self.args.get("spoolman_url", "http://192.168.4.124:7912")).rstrip("/")
        self.startup_delay_seconds = int(self.args.get("startup_delay_seconds", 8))
        self.debounce_seconds = int(self.args.get("debounce_seconds", 3))
        self.safety_poll_seconds = int(self.args.get("safety_poll_seconds", 600))
        self.debug_logs = bool(self.args.get("debug_logs", False))
        self.strict_mode_reregister = bool(self.args.get("strict_mode_reregister", False))
        self._color_distance_threshold = int(self.args.get("color_distance_threshold", COLOR_DISTANCE_THRESHOLD))
        self.evidence_log_path = str(self.args.get("evidence_log_path", "/config/ams_rfid_reconcile_evidence.log"))
        self.evidence_log_enabled = True
        self.last_slot_status = {}
        self.debounce_handle = None
        self.debounce_reasons = []
        self._active_run = None
        self._missing_helper_warned = set()
        self._pending_helper_warned = set()
        self._ensure_evidence_path_writable()

        for slot, entity_id in TRAY_ENTITY_BY_SLOT.items():
            self.listen_state(self._on_tray_state_change, entity_id, attribute="all")
            self.log(f"AMS RFID reconcile listening: slot={slot} entity={entity_id}")

        self.listen_event(self._on_reconcile_event, "bambu_rfid_reconcile_now")
        self.listen_event(self._on_reconcile_all_event, "AMS_RECONCILE_ALL")
        self.listen_state(self._on_manual_reconcile_button, "input_button.p1s_rfid_reconcile_now")
        self.listen_event(self._on_create_spool_event, "bambu_rfid_create_spool_from_tray")
        self.listen_event(self._on_manual_enroll_event, "bambu_rfid_manual_enroll_tag_to_spool")
        self.listen_event(self._on_validate_event, "AMS_RFID_VALIDATE")
        self.listen_event(self._on_homeassistant_start, "homeassistant_started")

        self.run_in(self._run_reconcile_startup, self.startup_delay_seconds)
        self.run_every(
            self._run_reconcile_poll,
            self.datetime() + datetime.timedelta(seconds=self.safety_poll_seconds),
            self.safety_poll_seconds,
        )
        self.log(f"AMS RFID reconcile initialized (evidence_log_path={self.evidence_log_path})")

    def _on_homeassistant_start(self, event_name, data, kwargs):
        self._schedule_reconcile("homeassistant_started")

    def _run_reconcile_startup(self, kwargs):
        self._run_reconcile("startup_delay")

    def _run_reconcile_poll(self, kwargs):
        self._run_reconcile("safety_poll")

    def _on_reconcile_event(self, event_name, data, kwargs):
        reason = str((data or {}).get("reason", "ui_button"))
        self._schedule_reconcile(reason)

    def _on_manual_reconcile_button(self, entity, attribute, old, new, kwargs):
        """Trigger full reconcile when input_button.p1s_rfid_reconcile_now state (ISO timestamp) changes. Same path as periodic/tray-trigger."""
        if not new or new == old:
            return
        if self._active_run is not None:
            self.log("MANUAL_RECONCILE_BUTTON skipped (reconcile already active)", level="INFO")
            return
        self.log(f"MANUAL_RECONCILE_BUTTON pressed state={new}", level="INFO")
        self._run_reconcile("manual_button")

    def _on_reconcile_all_event(self, event_name, data, kwargs):
        """Reconcile; when status_only=False, perform Spoolman writes (location, comment stamp). Fired by script.reconcile_all_ams_slots."""
        payload = data or {}
        reason = str(payload.get("reason", "manual_ui"))
        printer = str(payload.get("printer", ""))
        ts = str(payload.get("ts", ""))
        status_only = payload.get("status_only", True)
        if not isinstance(status_only, bool):
            status_only = True
        self.log(f"AMS_RECONCILE_ALL received reason={reason} printer={printer} ts={ts} status_only={status_only}")
        self._run_reconcile(reason, status_only=status_only)

    def _on_tray_state_change(self, entity, attribute, old, new, kwargs):
        slot = next((s for s, e in TRAY_ENTITY_BY_SLOT.items() if e == entity), None)
        if slot is not None:
            until_utc = datetime.datetime.utcnow() + datetime.timedelta(seconds=RFID_PENDING_SECONDS)
            self._set_rfid_pending_until(slot, until_utc)
        self._schedule_reconcile(f"tray_update:{entity}")

    def _on_create_spool_event(self, event_name, data, kwargs):
        payload = data or {}
        slot = self._safe_int(payload.get("slot"), 0)
        if slot not in TRAY_ENTITY_BY_SLOT:
            self._notify(
                "RFID Create Spool Failed",
                f"Invalid slot={slot}. Expected 1..6 (AMS1 + AMS_128/AMS_129).",
                notification_id="rfid_create_spool_invalid_slot",
            )
            return
        try:
            self._create_spool_from_tray(slot)
            self._run_reconcile(f"create_spool_slot_{slot}")
        except Exception as exc:
            self.log(f"create spool event failed: {exc}", level="ERROR")
            self._notify(
                "RFID Create Spool Failed",
                f"slot={slot} error={exc}",
                notification_id=f"rfid_create_spool_error_slot_{slot}",
            )

    def _on_manual_enroll_event(self, event_name, data, kwargs):
        payload = data or {}
        slot = self._safe_int(payload.get("slot"), 0)
        spool_id = self._safe_int(payload.get("spool_id"), 0)
        if slot not in TRAY_ENTITY_BY_SLOT or spool_id <= 0:
            self._notify(
                "RFID Manual Enroll Failed",
                f"Invalid inputs slot={slot} spool_id={spool_id}.",
                notification_id="rfid_manual_enroll_invalid_args",
            )
            return
        try:
            self._manual_enroll(slot, spool_id)
            self._run_reconcile(f"manual_enroll_slot_{slot}")
        except Exception as exc:
            self.log(f"manual enroll failed: {exc}", level="ERROR")
            self._notify(
                "RFID Manual Enroll Failed",
                f"slot={slot} spool_id={spool_id} error={exc}",
                notification_id=f"rfid_manual_enroll_error_slot_{slot}",
            )

    def _on_validate_event(self, event_name, data, kwargs):
        """Field validation runner: reconcile single slot and log compact transcript."""
        payload = data or {}
        slot = self._safe_int(payload.get("slot"), 0)
        mode = str(payload.get("mode", "reinsert")).strip()
        if slot not in TRAY_ENTITY_BY_SLOT:
            self._notify(
                "RFID Validate Failed",
                f"Invalid slot={slot}. Expected 1..6 (AMS1 + AMS_128/AMS_129).",
                notification_id="rfid_validate_invalid_slot",
            )
            return
        self._run_reconcile(f"validate_slot_{slot}_{mode}", slots_filter=[slot], validation_mode=True)

    def _schedule_reconcile(self, reason):
        self.debounce_reasons.append(reason)
        if self.debounce_handle is not None:
            self.cancel_timer(self.debounce_handle)
        self.debounce_handle = self.run_in(self._run_reconcile_debounced, self.debounce_seconds)

    def _run_reconcile_debounced(self, kwargs):
        reasons = ",".join(self.debounce_reasons[-10:])
        self.debounce_reasons = []
        self.debounce_handle = None
        self._run_reconcile(f"debounced:{reasons}")

    def _run_reconcile(self, reason, slots_filter=None, validation_mode=False, status_only=False):
        started = datetime.datetime.utcnow().isoformat() + "Z"
        self._active_run = {
            "reason": reason,
            "writes": [],
            "decisions": [],
            "no_write_paths": [],
            "conflicts": [],
            "unknown_tags": [],
            "auto_registers": [],
            "validation_transcripts": [],
            "spool_exists_cache": {},
        }
        if slots_filter is not None:
            raw = slots_filter if isinstance(slots_filter, (list, tuple)) else [slots_filter]
            slots_to_process = [self._safe_int(s, 0) for s in raw if self._safe_int(s, 0) in TRAY_ENTITY_BY_SLOT]
        else:
            slots_to_process = list(TRAY_ENTITY_BY_SLOT.keys())
        before_slots = {}
        for slot, entity_id in TRAY_ENTITY_BY_SLOT.items():
            if slot in slots_to_process:
                before_slots[str(slot)] = self._snapshot_slot(slot, entity_id)

        spools = self._spoolman_get("/api/v1/spool?limit=1000")
        if isinstance(spools, dict) and "items" in spools:
            spools = spools.get("items", [])
        if not isinstance(spools, list):
            raise RuntimeError("Spoolman /api/v1/spool did not return a list")

        # RFID UID map: eligible locations only (Shelf or AMS*; exclude Empty and New).
        tag_to_spools = {}
        for spool in spools:
            spool_id = self._safe_int(spool.get("id"), 0)
            if spool_id <= 0:
                continue
            loc = str(spool.get("location", "")).strip().lower()
            if loc == LOCATION_EMPTY.lower():
                continue
            if loc == "new":
                continue
            if loc != "shelf" and not loc.startswith("ams"):
                continue
            uid = self._extract_spool_uid(spool)
            if uid:
                tag_to_spools.setdefault(uid, []).append(spool_id)

        duplicate_uids = {uid for uid, spool_ids in tag_to_spools.items() if len(set(spool_ids)) > 1}
        spool_index = {self._safe_int(s.get("id"), 0): s for s in spools}

        ok = 0
        unbound = 0
        conflict = 0
        mismatch = 0

        for slot, entity_id in TRAY_ENTITY_BY_SLOT.items():
            if slot not in slots_to_process:
                continue
            writes_before_slot = len(self._active_run["writes"])
            tray = self.get_state(entity_id, attribute="all") or {}
            attrs = tray.get("attributes", {}) if isinstance(tray, dict) else {}
            raw_tag = attrs.get("tag_uid")
            tag_uid = self._canonicalize_tag_uid(raw_tag)
            tray_meta = self._tray_meta(attrs, tray.get("state", ""))
            status = "UNBOUND (no tag)"  # initial; updated to STATUS_* in branches
            resolved_spool_id = 0

            tray_state_str = str(tray.get("state", "")).strip().lower() if isinstance(tray, dict) else ""
            tray_empty = tray_state_str == "empty"

            t = {
                "slot": slot,
                "tray_state": tray_state_str,
                "sensor_ok": tray_state_str not in ("unknown", "unavailable", ""),
                "empty": tray_empty,
                "has_tag_uid": bool(tag_uid),
                "tag_uid": tag_uid or "",
                "tray_meta": f"name={tray_meta.get('name','')} type={tray_meta.get('type','')} color_hex={tray_meta.get('color_hex','')} filament_id={tray_meta.get('filament_id','')}",
                "uid_lookup_count": 0,
                "metadata_candidate_ids": [],
                "candidate_weights": {},
                "decision": "",
                "reason": "",
                "action": "",
                "selected_spool_id": 0,
                "final_slot_status": "",
                "writes_performed": [],
                "final_spool_id": 0,
                "final_location": "",
                "raw_tag_uid": str(raw_tag) if raw_tag is not None else "",
            }

            # Sticky mapping: tray identity (tray_uuid if present else tag_uid) for same-tray no-flip
            current_tray_sig = self._get_tray_identity(attrs, tag_uid or "", tray_state_str)
            stored_tray_sig = (self.get_state(f"input_text.ams_slot_{slot}_tray_signature") or "") or ""
            helper_spool_id = self._safe_int(self.get_state(f"input_text.ams_slot_{slot}_spool_id"), 0)
            previous_helper_spool_id = helper_spool_id
            helper_expected = self._safe_int(self.get_state(f"input_text.ams_slot_{slot}_expected_spool_id"), 0)

            # TRUTH GUARD (RFID_VISIBLE): clear stale helper if its UID doesn't match physical tray tag
            norm_tag_tg = _normalize_rfid_tag_uid(tag_uid)
            rfid_visible = bool(norm_tag_tg and norm_tag_tg != "0000000000000000")

            # ── RFID identity-stuck tracker ──
            import time as _time_mod
            _rit = getattr(self, "_rfid_identity_tracker", None)
            if _rit is None:
                self._rfid_identity_tracker = {}
                _rit = self._rfid_identity_tracker
            _prev_entry = _rit.get(slot)
            if _prev_entry is None or _prev_entry["identity"] != current_tray_sig:
                _rit[slot] = {"identity": current_tray_sig, "change_ts": _time_mod.time()}
            if (reason.startswith("manual") and rfid_visible and not tray_empty
                    and _prev_entry is not None and _prev_entry["identity"] == current_tray_sig
                    and (_time_mod.time() - _prev_entry["change_ts"]) >= RFID_STUCK_SECONDS):
                status = STATUS_RFID_IDENTITY_STUCK
                t["decision"], t["reason"], t["action"] = "STUCK", "rfid_identity_stuck", "rfid_identity_stuck"
                t["unbound_reason"] = UNBOUND_RFID_NOT_REFRESHED
                self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_RFID_NOT_REFRESHED)
                self._log_slot_status_change(slot, status, tag_uid or "", helper_spool_id, tray_meta)
                t["final_slot_status"], t["final_spool_id"] = status, helper_spool_id
                self._active_run["validation_transcripts"].append(t)
                if validation_mode:
                    self._log_validation_transcript(t)
                unbound += 1
                continue

            if rfid_visible and helper_spool_id > 0:
                helper_spool_obj_tg = spool_index.get(helper_spool_id) or {}
                if not self._truth_guard_slot_patch(slot, t, tray_meta, tag_uid, helper_spool_id, helper_spool_obj_tg, tray_empty, tray_state_str):
                    helper_spool_id = 0
                    previous_helper_spool_id = 0

            # Bound invariant wins over pending: spool_id == expected_spool_id > 0 -> stay NON_RFID_REGISTERED
            if not tag_uid and helper_spool_id > 0 and helper_expected > 0 and helper_spool_id == helper_expected:
                status = STATUS_NON_RFID_REGISTERED
                t["decision"], t["reason"], t["action"] = "NON_RFID", "bound_invariant", "nonrfid_registered"
                self._set_helper(f"input_text.ams_slot_{slot}_rfid_pending_until", "")
                if not tray_empty:
                    self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
                if not status_only:
                    self._force_location_and_helpers(
                        slot, helper_spool_id, "", source="nonrfid_converge_location",
                        tray_meta=tray_meta, tray_state=tray.get("state", ""),
                        tray_identity=current_tray_sig or stored_tray_sig or None,
                        previous_helper_spool_id=0,
                        spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                    )
                    self._record_decision(slot, "nonrfid_converge_location", {"spool_id": helper_spool_id})
                self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                self._log_slot_status_change(slot, status, tag_uid or "", helper_spool_id, tray_meta)
                ok += 1
                t["final_slot_status"] = status
                t["final_spool_id"] = helper_spool_id
                t["final_location"] = CANONICAL_LOCATION_BY_SLOT.get(slot, "")
                fid = helper_spool_id
                if _should_converge_ha_sig(status_only, status, fid):
                    self._converge_ha_sig(slot, fid, tray_meta, spool_index, reason="nonrfid_converge", tag_uid="", tray_empty=tray_empty, tray_state_str=tray_state_str)
                self._active_run["validation_transcripts"].append(t)
                if validation_mode:
                    self._log_validation_transcript(t)
                continue

            # Pending demotion: identity unavailable + actually pending + valid helper + stale/zero expected
            raw_tag_uid_pd = attrs.get("tag_uid") if attrs.get("tag_uid") is not None else ""
            raw_tray_uuid_pd = attrs.get("tray_uuid") if attrs.get("tray_uuid") is not None else ""
            identity_unavailable = not tag_uid and self._is_all_zero_identity(raw_tag_uid_pd, raw_tray_uuid_pd)
            stored_status = (self.get_state(f"input_text.ams_slot_{slot}_status") or "").strip()
            pending_until_raw = (self.get_state(f"input_text.ams_slot_{slot}_rfid_pending_until") or "").strip()
            actually_pending = stored_status == STATUS_PENDING_RFID_READ or bool(pending_until_raw)
            stale_expected = helper_expected == 0 or helper_expected != helper_spool_id
            if identity_unavailable and not tray_empty and helper_spool_id > 0 and actually_pending and stale_expected:
                status = STATUS_NON_RFID_REGISTERED
                t["decision"], t["reason"], t["action"] = "NON_RFID", "pending_demote_identity_unavailable", "nonrfid_pending_demoted"
                self.log(
                    f"PENDING_DEMOTE slot={slot} helper_spool_id={helper_spool_id} stale_expected={helper_expected} "
                    f"stored_status={stored_status} pending_until={pending_until_raw!r} -> {status}",
                    level="INFO",
                )
                self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                self._set_helper(f"input_text.ams_slot_{slot}_rfid_pending_until", "")
                if not status_only:
                    self._force_location_and_helpers(
                        slot, helper_spool_id, "", source="nonrfid_converge_location",
                        tray_meta=tray_meta, tray_state=tray.get("state", ""),
                        tray_identity=current_tray_sig or stored_tray_sig or None,
                        previous_helper_spool_id=0,
                        spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                    )
                    self._record_decision(slot, "nonrfid_pending_demote", {"spool_id": helper_spool_id, "stale_expected": helper_expected})
                self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                self._log_slot_status_change(slot, status, tag_uid or "", helper_spool_id, tray_meta)
                ok += 1
                t["final_slot_status"] = status
                t["final_spool_id"] = helper_spool_id
                t["final_location"] = CANONICAL_LOCATION_BY_SLOT.get(slot, "")
                fid = helper_spool_id
                if _should_converge_ha_sig(status_only, status, fid):
                    self._converge_ha_sig(slot, fid, tray_meta, spool_index, reason="nonrfid_pending_demote", tag_uid="", tray_empty=tray_empty, tray_state_str=tray_state_str)
                self._active_run["validation_transcripts"].append(t)
                if validation_mode:
                    self._log_validation_transcript(t)
                continue

            # PENDING_RFID_READ only when no valid tag and tray not empty and pending_until in future
            if not tag_uid and not tray_empty:
                pending_until = self._get_rfid_pending_until(slot)
                now_utc = datetime.datetime.utcnow()
                if pending_until is not None and now_utc < pending_until:
                    status = STATUS_PENDING_RFID_READ
                    t["decision"], t["reason"], t["action"] = "PENDING", "rfid_pending", "pending_rfid_read"
                    self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                    self._log_slot_status_change(slot, status, tag_uid or "", 0, tray_meta)
                    self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str)
                    t["final_slot_status"] = status
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    continue

            # HT non-RFID override: run before any tag_uid-based branching so normalized "" or literal 000...0 both hit HT path
            raw_tag_uid_ht = attrs.get("tag_uid") if attrs.get("tag_uid") is not None else ""
            raw_tray_uuid_ht = attrs.get("tray_uuid") if attrs.get("tray_uuid") is not None else ""
            nonrfid_enabled = (self.get_state("input_boolean.p1s_nonrfid_enabled") or "").strip().lower() == "on"
            if nonrfid_enabled and not tray_empty and slot in (5, 6) and self._is_all_zero_identity(raw_tag_uid_ht, raw_tray_uuid_ht):
                self.log(
                    f"HT_GUARD_HIT slot={slot} empty={tray_empty} raw_tag_uid={raw_tag_uid_ht!r} raw_tray_uuid={raw_tray_uuid_ht!r}",
                    level="INFO",
                )
                helper_entity = f"input_text.ams_slot_{slot}_spool_id"
                raw = self.get_state(helper_entity)
                try:
                    helper_spool_id = int(raw or 0)
                except (ValueError, TypeError, AttributeError):
                    helper_spool_id = 0
                self.log(
                    f"HT_HELPER_READ slot={slot} entity_id={helper_entity} raw={raw!r} parsed={helper_spool_id}",
                    level="INFO",
                )
                # ── HT fingerprint + pending confirmation ──
                ht_fp = self._compute_ht_fingerprint(attrs, tray_state_str)
                ht_confirmed, ht_pending = self._check_ht_pending(slot, ht_fp, stored_tray_sig)

                if ht_pending:
                    status = STATUS_WAITING_CONFIRMATION
                    t["decision"], t["reason"], t["action"] = "NON_RFID", "pending_fingerprint", "waiting_confirmation"
                    self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                    self._log_slot_status_change(slot, status, "", helper_spool_id, tray_meta)
                    t["final_slot_status"], t["final_spool_id"] = status, helper_spool_id
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    continue

                fp_changed = not stored_tray_sig.startswith("PENDING:") and ht_fp != stored_tray_sig and stored_tray_sig != ""
                just_confirmed = stored_tray_sig.startswith("PENDING:")
                ht_needs_rematch = fp_changed or just_confirmed or helper_spool_id <= 0

                if not ht_needs_rematch and helper_spool_id > 0:
                    # Fingerprint stable, existing binding — validate helper spool (existing logic)
                    helper_valid = True
                    try:
                        spool_resp = self._spoolman_get(f"/api/v1/spool/{helper_spool_id}")
                        if not isinstance(spool_resp, dict) or self._safe_int(spool_resp.get("id"), 0) != helper_spool_id:
                            helper_valid = False
                    except RuntimeError as exc:
                        err = str(exc)
                        if "HTTP 404" in err:
                            helper_valid = False
                            self.log(
                                f"CLEAR_MISSING_HELPER_SPOOL slot={slot} helper_spool_id={helper_spool_id} http=404 -> clearing to 0",
                                level="INFO",
                            )
                            if not status_only:
                                self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                            status = "NON_RFID_UNREGISTERED"
                            t["decision"], t["reason"], t["action"] = "NON_RFID", "helper_spool_not_found", "nonrfid_ht_helper_cleared"
                            t["unbound_reason"], t["unbound_detail"] = UNBOUND_HELPER_SPOOL_NOT_FOUND, f"helper_spool_id={helper_spool_id} http=404"
                            self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                            self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_HELPER_SPOOL_NOT_FOUND)
                            self._log_slot_status_change(slot, status, "", 0, tray_meta)
                            t["final_slot_status"], t["final_spool_id"] = status, 0
                            self._active_run["validation_transcripts"].append(t)
                            if validation_mode:
                                self._log_validation_transcript(t)
                            unbound += 1
                            continue
                        else:
                            status = "NON_RFID_UNREGISTERED"
                            t["decision"], t["reason"], t["action"] = "NON_RFID", "spoolman_lookup_failed", "nonrfid_ht_lookup_failed"
                            t["unbound_reason"], t["unbound_detail"] = UNBOUND_SPOOLMAN_LOOKUP_FAILED, err[:80]
                            self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                            self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_SPOOLMAN_LOOKUP_FAILED)
                            self._log_slot_status_change(slot, status, "", helper_spool_id, tray_meta)
                            t["final_slot_status"], t["final_spool_id"] = status, helper_spool_id
                            self._active_run["validation_transcripts"].append(t)
                            if validation_mode:
                                self._log_validation_transcript(t)
                            unbound += 1
                            continue
                    if not helper_valid:
                        self.log(
                            f"CLEAR_MISSING_HELPER_SPOOL slot={slot} helper_spool_id={helper_spool_id} http=404 -> clearing to 0",
                            level="INFO",
                        )
                        if not status_only:
                            self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                        status = "NON_RFID_UNREGISTERED"
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "helper_spool_not_found", "nonrfid_ht_helper_cleared"
                        t["unbound_reason"], t["unbound_detail"] = UNBOUND_HELPER_SPOOL_NOT_FOUND, f"helper_spool_id={helper_spool_id} http=404"
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_HELPER_SPOOL_NOT_FOUND)
                        self._log_slot_status_change(slot, status, "", 0, tray_meta)
                        t["final_slot_status"], t["final_spool_id"] = status, 0
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        unbound += 1
                        continue
                    if not self._truth_guard_slot_patch(slot, t, tray_meta, "", helper_spool_id, spool_resp, tray_empty, tray_state_str):
                        if not status_only:
                            self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                        status = "NON_RFID_UNREGISTERED"
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "truth_guard_material_mismatch", "nonrfid_ht_material_mismatch"
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, "", 0, tray_meta)
                        t["final_slot_status"], t["final_spool_id"] = status, 0
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        unbound += 1
                        continue
                    if not status_only:
                        self._force_location_and_helpers(
                            slot, helper_spool_id, "", source="nonrfid_ht_present",
                            tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=ht_fp,
                            previous_helper_spool_id=previous_helper_spool_id,
                            spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                        )
                        self._spoolman_patch(f"/api/v1/spool/{helper_spool_id}", {"comment": f"ha_sig={ht_fp}"[:255]})
                    status = STATUS_OK
                    ok += 1
                    t["decision"], t["reason"], t["action"] = "NON_RFID", "ht_present", "nonrfid_ht_registered"
                    t["final_spool_id"], t["selected_spool_id"] = helper_spool_id, helper_spool_id
                    t["final_slot_status"] = status
                    t["final_location"] = CANONICAL_LOCATION_BY_SLOT.get(slot, "")
                    self.log(f"HT_NONRFID_REGISTERED slot={slot} helper_spool_id={helper_spool_id}", level="DEBUG")
                    self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                    self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
                    self._log_slot_status_change(slot, status, "", helper_spool_id, tray_meta)
                    self._record_decision(slot, "nonrfid_ht_present", {"helper_spool_id": helper_spool_id})
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    continue

                # ── Fingerprint changed / just confirmed / unbound → auto-match if confident ──
                confident = self._is_confident_nonrfid(attrs, tray_state_str)
                if confident:
                    shelf_ids, _ = self._find_deterministic_candidates(spools, tray_meta, slot)
                    if len(shelf_ids) == 1:
                        resolved = shelf_ids[0]
                        if not status_only:
                            self._force_location_and_helpers(
                                slot, resolved, "", source="nonrfid_ht_auto_match",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=ht_fp,
                                previous_helper_spool_id=previous_helper_spool_id,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                            )
                        status = STATUS_OK_NONRFID
                        ok += 1
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "ht_auto_match", "nonrfid_ht_auto_match"
                        t["final_spool_id"], t["selected_spool_id"] = resolved, resolved
                        t["final_slot_status"] = status
                        t["final_location"] = CANONICAL_LOCATION_BY_SLOT.get(slot, "")
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
                        self._log_slot_status_change(slot, status, "", resolved, tray_meta)
                        self._record_decision(slot, "nonrfid_ht_auto_match", {"resolved_spool_id": resolved})
                    else:
                        status = STATUS_NEEDS_MANUAL_BIND
                        if not status_only:
                            self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                            self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_NONRFID_NO_MATCH)
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, "", 0, tray_meta)
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "ht_no_match", "needs_manual_bind"
                        t["unbound_reason"] = UNBOUND_NONRFID_NO_MATCH
                        t["final_spool_id"] = 0
                        notified = getattr(self, "_ht_nomatch_notified", None)
                        if notified is None:
                            self._ht_nomatch_notified = set()
                            notified = self._ht_nomatch_notified
                        fp_key = f"{slot}:{ht_fp}"
                        if fp_key not in notified:
                            notified.add(fp_key)
                            self.log(f"HT_NONRFID_NO_MATCH slot={slot} fingerprint={ht_fp} -> NEEDS_MANUAL_BIND", level="WARNING")
                        unbound += 1
                else:
                    status = STATUS_LOW_CONFIDENCE
                    self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_LOW_CONFIDENCE)
                    self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                    self._log_slot_status_change(slot, status, "", 0, tray_meta)
                    t["decision"], t["reason"], t["action"] = "NON_RFID", "low_confidence", "low_confidence_no_auto_match"
                    t["unbound_reason"] = UNBOUND_LOW_CONFIDENCE
                    t["final_spool_id"] = 0
                    unbound += 1
                t["final_slot_status"] = status
                self._active_run["validation_transcripts"].append(t)
                if validation_mode:
                    self._log_validation_transcript(t)
                continue

            # Harden: if tray_uuid temporarily missing but tag_uid (normalized) equals stored_sig, treat as same tray
            if not self._has_tray_uuid(attrs) and stored_tray_sig:
                tag_norm = self._norm_tray_identity_tag(tag_uid or "")
                if tag_norm == stored_tray_sig:
                    current_tray_sig = stored_tray_sig

            # Tray change detection (tray identity change) → start 20s pending window
            if isinstance(stored_tray_sig, str) and current_tray_sig != stored_tray_sig:
                until_utc = datetime.datetime.utcnow() + datetime.timedelta(seconds=RFID_PENDING_SECONDS)
                self._set_rfid_pending_until(slot, until_utc)

            # Deterministic invariant: empty tray → clear sticky expected state (physical slots 1–4 only).
            if tray_empty:
                self._clear_expected_for_slot(slot, "tray_empty")

            # Defensive: slot must have a canonical location or we can never persist OK
            if slot not in CANONICAL_LOCATION_BY_SLOT:
                self.log(
                    f"slot={slot} is in TRAY_ENTITY_BY_SLOT but missing from CANONICAL_LOCATION_BY_SLOT; cannot persist (missing_canonical_location)",
                    level="ERROR",
                )
                status = STATUS_CONFLICT_MISSING_CANONICAL
                conflict += 1
                t["decision"], t["reason"], t["action"] = "CONFLICT", "missing_canonical_location", "missing_canonical_location"
                self._record_no_write(slot, "missing_canonical_location", {"slot": slot})
                self._active_run["conflicts"].append({
                    "slot": slot,
                    "tag_uid": tag_uid,
                    "reason": "missing_canonical_location",
                    "payload": {"slot": slot},
                })
            # Tray unavailable/unknown: set UNBOUND:TRAY_UNAVAILABLE and skip rest
            elif tray_state_str in ("unknown", "unavailable", ""):
                status = STATUS_UNBOUND_TRAY_UNAVAILABLE
                t["decision"], t["reason"], t["action"] = "UNBOUND", "TRAY_UNAVAILABLE", "unbound_tray_unavailable"
                self._record_no_write(slot, "tray_unavailable")
            elif not tag_uid:
                # Bound invariant and PENDING_RFID_READ already handled at top level; HT override for 5/6 also at top level
                # Non-RFID stable state: spool_id already set and expected_spool_id 0/empty -> treat as assigned non-RFID slot (no rfid_pending_until required)
                if not tray_empty:
                    if helper_spool_id > 0 and helper_expected == 0:
                        status = STATUS_NON_RFID_REGISTERED
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "non_rfid_stable", "nonrfid_registered"
                        if not status_only:
                            nonrfid_tray_sig = current_tray_sig or self._build_tray_signature(tray_meta, tray.get("state", ""), "")
                            self._force_location_and_helpers(
                                slot, helper_spool_id, "", source="nonrfid_converge_location",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""),
                                tray_identity=nonrfid_tray_sig,
                                previous_helper_spool_id=0,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                            )
                            self._record_decision(slot, "nonrfid_converge_location", {"spool_id": helper_spool_id})
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, tag_uid or "", helper_spool_id, tray_meta)
                        ok += 1
                        t["final_slot_status"] = status
                        t["final_spool_id"] = helper_spool_id
                        t["final_location"] = CANONICAL_LOCATION_BY_SLOT.get(slot, "")
                        fid = helper_spool_id
                        if _should_converge_ha_sig(status_only, status, fid):
                            self._converge_ha_sig(slot, fid, tray_meta, spool_index, reason="nonrfid_converge", tag_uid="", tray_empty=tray_empty, tray_state_str=tray_state_str)
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue
                status = STATUS_UNBOUND_NO_TAG
                t["decision"], t["reason"], t["action"] = "UNBOUND", "no_tag", "unbound_no_tag"
                self._record_no_write(slot, "no_tag_uid")
                nonrfid_enabled = (self.get_state("input_boolean.p1s_nonrfid_enabled") or "").strip().lower() == "on"
                if nonrfid_enabled and not tray_empty:
                    # PHASE_2_6: Non-RFID deterministic matching (Shelf-first, controlled New fallback). HT all-zero case handled earlier.
                    shelf_ids, _ = self._find_deterministic_candidates(spools, tray_meta, slot)
                    nonrfid_tray_sig = current_tray_sig or self._build_tray_signature(tray_meta, tray.get("state", ""), "")
                    if len(shelf_ids) == 1:
                        resolved_spool_id = shelf_ids[0]
                        if not status_only:
                            if self._should_stick(slot, nonrfid_tray_sig, stored_tray_sig, helper_spool_id) and helper_spool_id == resolved_spool_id:
                                resolved_spool_id = helper_spool_id
                            self._force_location_and_helpers(
                                slot, resolved_spool_id, "", source="nonrfid_shelf_match",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=nonrfid_tray_sig,
                                previous_helper_spool_id=previous_helper_spool_id,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                            )
                        status = STATUS_OK
                        ok += 1
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "shelf_match", "nonrfid_shelf_bind"
                        t["final_spool_id"], t["selected_spool_id"] = resolved_spool_id, resolved_spool_id
                        t["final_slot_status"] = status
                        t["final_location"] = CANONICAL_LOCATION_BY_SLOT.get(slot, "")
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, "", resolved_spool_id, tray_meta)
                        self._record_decision(slot, "nonrfid_shelf_match", {"resolved_spool_id": resolved_spool_id})
                        fid = int(resolved_spool_id or 0)
                        if _should_converge_ha_sig(status_only, status, fid):
                            self._converge_ha_sig(slot, fid, tray_meta, spool_index, reason="nonrfid_shelf_match", tag_uid=tag_uid or "", tray_empty=tray_empty, tray_state_str=tray_state_str)
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue
                    if len(shelf_ids) > 1:
                        candidate_spool_dicts = []
                        for sid in shelf_ids:
                            spool = spool_index.get(sid) or self._spoolman_get(f"/api/v1/spool/{sid}")
                            if isinstance(spool, dict) and self._safe_float(spool.get("remaining_weight"), -1) > 0:
                                candidate_spool_dicts.append(spool)
                        winner_id, _ = tiebreak_choose_spool(candidate_spool_dicts, strict_mode=False)
                        if winner_id is not None:
                            resolved_spool_id = winner_id
                            if not status_only:
                                self._force_location_and_helpers(
                                    slot, resolved_spool_id, "", source="nonrfid_shelf_tiebreak",
                                    tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=nonrfid_tray_sig,
                                    previous_helper_spool_id=previous_helper_spool_id,
                                    spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                                )
                            status = STATUS_OK
                            ok += 1
                            t["decision"], t["reason"], t["action"] = "NON_RFID", "shelf_tiebreak", "nonrfid_shelf_bind"
                            t["final_spool_id"], t["selected_spool_id"] = resolved_spool_id, resolved_spool_id
                            t["final_slot_status"] = status
                            t["final_location"] = CANONICAL_LOCATION_BY_SLOT.get(slot, "")
                            self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                            self._log_slot_status_change(slot, status, "", resolved_spool_id, tray_meta)
                            self._record_decision(slot, "nonrfid_shelf_tiebreak", {"resolved_spool_id": resolved_spool_id})
                            fid = int(resolved_spool_id or 0)
                            if _should_converge_ha_sig(status_only, status, fid):
                                self._converge_ha_sig(slot, fid, tray_meta, spool_index, reason="nonrfid_shelf_tiebreak", tag_uid=tag_uid or "", tray_empty=tray_empty, tray_state_str=tray_state_str)
                            self._active_run["validation_transcripts"].append(t)
                            if validation_mode:
                                self._log_validation_transcript(t)
                            continue
                        status = STATUS_UNBOUND_ACTION_REQUIRED
                        unbound += 1
                        t["decision"], t["reason"], t["action"] = "UNBOUND", "nonrfid_ambiguous_shelf", "unbound_needs_action"
                        t["final_slot_status"] = status
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str)
                        self._notify_nonrfid_needs_action(slot, tray_meta, "Multiple Shelf candidates; tie-break did not pick one.")
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue
                    new_ids = self._find_deterministic_candidates_new_only(spools, tray_meta, slot)
                    if len(new_ids) == 1:
                        resolved_spool_id = new_ids[0]
                        if not status_only:
                            self._force_location_and_helpers(
                                slot, resolved_spool_id, "", source="nonrfid_new_fallback",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=nonrfid_tray_sig,
                                previous_helper_spool_id=previous_helper_spool_id,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                            )
                        self._notify_nonrfid_new_fallback(slot, resolved_spool_id, tray_meta)
                        status = STATUS_OK
                        ok += 1
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "new_fallback", "nonrfid_new_fallback_bind"
                        t["final_spool_id"], t["selected_spool_id"] = resolved_spool_id, resolved_spool_id
                        t["final_slot_status"] = status
                        t["final_location"] = CANONICAL_LOCATION_BY_SLOT.get(slot, "")
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, "", resolved_spool_id, tray_meta)
                        self._record_decision(slot, "nonrfid_new_fallback", {"resolved_spool_id": resolved_spool_id})
                        fid = int(resolved_spool_id or 0)
                        if _should_converge_ha_sig(status_only, status, fid):
                            self._converge_ha_sig(slot, fid, tray_meta, spool_index, reason="nonrfid_new_fallback", tag_uid=tag_uid or "", tray_empty=tray_empty, tray_state_str=tray_state_str)
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue
                    status = STATUS_UNBOUND_ACTION_REQUIRED
                    unbound += 1
                    reason_detail = "No Shelf match and no single New candidate." if len(new_ids) == 0 else "No Shelf match; ambiguous New candidates."
                    t["decision"], t["reason"], t["action"] = "UNBOUND", "nonrfid_no_match", "unbound_needs_action"
                    t["final_slot_status"] = status
                    self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                    self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str)
                    self._notify_nonrfid_needs_action(slot, tray_meta, reason_detail)
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    continue
            else:
                # All slots: write tray identity when tray has data (sticky key)
                if tray_state_str not in ("unknown", "unavailable", "", "empty") and current_tray_sig:
                    self._set_helper(f"input_text.ams_slot_{slot}_tray_signature", current_tray_sig)
                    if slot in (5, 6):
                        self.log(
                            f"RECONCILE_SIG slot={slot} tray_entity={entity_id} tag_uid={tag_uid} "
                            f"tray_sig={current_tray_sig[:64]}{'...' if len(current_tray_sig) > 64 else ''} wrote=true reason=tray_data_present",
                            level="DEBUG",
                        )
                slot_tag = _normalize_rfid_tag_uid(tag_uid)
                mapped_ids = list(set(tag_to_spools.get(slot_tag, [])))
                if len(mapped_ids) == 1:
                    resolved_spool_id = mapped_ids[0]
                    uid_matched = resolved_spool_id > 0
                    expected_spool_id = self._safe_int(self.get_state(f"input_text.ams_slot_{slot}_expected_spool_id"), 0)
                    expected_vs_resolved_mismatch = expected_spool_id not in (0, resolved_spool_id)
                    # Color mismatch only when tray_hex is authoritative (skip "", "000000", "00000000", "none", "unknown").
                    tray_hex = self._normalize_tray_hex(tray_meta.get("color_hex"))
                    spool_obj = spool_index.get(resolved_spool_id, {}) or {}
                    filament = spool_obj.get("filament", {}) if isinstance(spool_obj.get("filament"), dict) else {}
                    spool_color = (str(filament.get("color_hex") or "")).strip().lower().replace("#", "")
                    if len(spool_color) == 8:
                        spool_color = spool_color[:6]
                    if not self._is_tray_hex_authoritative(tray_hex):
                        color_mismatch = False
                    else:
                        thresh = getattr(self, "_color_distance_threshold", COLOR_DISTANCE_THRESHOLD)
                        close, dist, thresh = _colors_close(tray_hex, spool_color, thresh)
                        if close:
                            color_mismatch = False
                            if self.debug_logs:
                                self.log(
                                    f"COLOR_TOLERANCE_ACCEPTED slot={slot} dist={dist:.1f} threshold={thresh} tray={tray_hex} spool={spool_color}",
                                    level="DEBUG",
                                )
                        elif dist >= 0:
                            color_mismatch = True
                        else:
                            # One or both unparseable: preserve strict behavior
                            color_mismatch = (spool_color != "" and tray_hex != spool_color)
                    mismatch_detected = (expected_vs_resolved_mismatch or color_mismatch) if uid_matched else False
                    if uid_matched and mismatch_detected:
                        if expected_vs_resolved_mismatch and not color_mismatch:
                            # Auto-heal: expected_spool_id is stale, UID says resolved. Trust RFID.
                            if not status_only:
                                if self._may_stick_override(slot, resolved_spool_id, helper_spool_id, tag_uid, spool_index, current_tray_sig, stored_tray_sig):
                                    resolved_spool_id = helper_spool_id
                                if not self._rfid_bind_guard_ok(resolved_spool_id, tag_uid, spool_index):
                                    self._apply_rfid_bind_guard_fail(slot, t, tray_meta, tag_uid, resolved_spool_id, validation_mode)
                                    unbound += 1
                                    continue
                                self._force_location_and_helpers(
                                    slot, resolved_spool_id, tag_uid, source="expected_autofix",
                                    tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=current_tray_sig,
                                    previous_helper_spool_id=previous_helper_spool_id,
                                    spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                                )
                            t["converge_reason"] = "expected_autofix"
                            status = STATUS_OK_FIXED_EXPECTED
                            ok += 1
                            t["decision"], t["reason"], t["action"] = "OK", "FIXED_EXPECTED", "expected_autofix"
                            t["uid_lookup_count"], t["final_spool_id"], t["selected_spool_id"] = 1, resolved_spool_id, resolved_spool_id
                            t["final_location"] = CANONICAL_LOCATION_BY_SLOT[slot]
                            self._record_decision(
                                slot,
                                "expected_autofix",
                                {"tag_uid": tag_uid, "resolved_spool_id": resolved_spool_id, "previous_expected": expected_spool_id},
                            )
                            expected_hex = self._normalize_tray_hex(self.get_state(f"input_text.ams_slot_{slot}_expected_color_hex"))
                            self._maybe_log_color_warning(slot, expected_hex, tray_hex)
                        else:
                            self.log(
                                f"RFID_MISMATCH_DEBUG slot={slot} tag_uid={tag_uid} resolved={resolved_spool_id} expected={expected_spool_id} "
                                f"expected_mismatch={expected_vs_resolved_mismatch} tray_hex='{tray_hex}' spool_color='{spool_color}' color_mismatch={color_mismatch}",
                                level="WARNING",
                            )
                            status = STATUS_MISMATCH
                            mismatch += 1
                            t["decision"], t["reason"], t["action"] = "CONFLICT", "MISMATCH", "conflict_mismatch"
                            t["uid_lookup_count"], t["final_spool_id"], t["selected_spool_id"] = 1, resolved_spool_id, resolved_spool_id
                            self._record_no_write(
                                slot,
                                "mismatch_expected_or_color",
                                {"expected_spool_id": expected_spool_id, "resolved_spool_id": resolved_spool_id, "tray_hex": tray_hex, "spool_color": spool_color},
                            )
                    elif uid_matched and not mismatch_detected:
                        if not status_only:
                            if self._may_stick_override(slot, resolved_spool_id, helper_spool_id, tag_uid, spool_index, current_tray_sig, stored_tray_sig):
                                resolved_spool_id = helper_spool_id
                            if not self._rfid_bind_guard_ok(resolved_spool_id, tag_uid, spool_index):
                                self._apply_rfid_bind_guard_fail(slot, t, tray_meta, tag_uid, resolved_spool_id, validation_mode)
                                unbound += 1
                                continue
                            self._force_location_and_helpers(
                                slot, resolved_spool_id, tag_uid, source="known_binding",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=current_tray_sig,
                                previous_helper_spool_id=previous_helper_spool_id,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                            )
                        t["converge_reason"] = "known_binding"
                        status = STATUS_OK
                        ok += 1
                        t["decision"], t["reason"], t["action"] = "OK", "known_binding", "known_uid_bind"
                        t["uid_lookup_count"], t["final_spool_id"], t["selected_spool_id"] = 1, resolved_spool_id, resolved_spool_id
                        t["final_location"] = CANONICAL_LOCATION_BY_SLOT[slot]
                        self._record_decision(
                            slot,
                            "known_binding",
                            {"tag_uid": tag_uid, "resolved_spool_id": resolved_spool_id, "result": "ok"},
                        )
                        expected_hex = self._normalize_tray_hex(self.get_state(f"input_text.ams_slot_{slot}_expected_color_hex"))
                        self._maybe_log_color_warning(slot, expected_hex, tray_hex)
                elif len(mapped_ids) > 1:
                    # PHASE_2_5: tie-break by least remaining grams
                    candidate_spool_dicts = []
                    for sid in mapped_ids:
                        spool = spool_index.get(sid) or self._spoolman_get(f"/api/v1/spool/{sid}")
                        spool_g = self._safe_float(spool.get("remaining_weight"), -1.0)
                        if spool_g <= 0:
                            continue
                        candidate_spool_dicts.append(spool)
                    winner_id, tiebreak_reason = tiebreak_choose_spool(candidate_spool_dicts, strict_mode=False)
                    if winner_id is not None:
                        resolved_spool_id = winner_id
                        t["uid_lookup_count"], t["metadata_candidate_ids"] = len(mapped_ids), mapped_ids
                        t["decision"], t["reason"], t["action"] = "OK", "rfid_shelf_tiebreak_least_remaining", "rfid_shelf_tiebreak"
                        t["final_spool_id"], t["selected_spool_id"], t["final_location"] = resolved_spool_id, resolved_spool_id, CANONICAL_LOCATION_BY_SLOT[slot]
                        if not status_only:
                            if self._may_stick_override(slot, resolved_spool_id, helper_spool_id, tag_uid, spool_index, current_tray_sig, stored_tray_sig):
                                resolved_spool_id = helper_spool_id
                            if not self._rfid_bind_guard_ok(resolved_spool_id, tag_uid, spool_index):
                                self._apply_rfid_bind_guard_fail(slot, t, tray_meta, tag_uid, resolved_spool_id, validation_mode)
                                unbound += 1
                                continue
                            self._force_location_and_helpers(
                                slot, resolved_spool_id, tag_uid, source="rfid_shelf_tiebreak",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=current_tray_sig,
                                previous_helper_spool_id=previous_helper_spool_id,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                            )
                        t["converge_reason"] = "rfid_shelf_tiebreak"
                        status = STATUS_OK
                        ok += 1
                        self._record_decision(slot, "rfid_shelf_tiebreak", {"tag_uid": tag_uid, "resolved_spool_id": resolved_spool_id, "reason": tiebreak_reason})
                    else:
                        status = STATUS_CONFLICT_DUPLICATE_UID
                        conflict += 1
                        t["decision"], t["reason"], t["action"] = "CONFLICT", "DUPLICATE_UID", "conflict_duplicate_uid"
                        t["uid_lookup_count"], t["metadata_candidate_ids"] = len(mapped_ids), mapped_ids
                        self._active_run["conflicts"].append(
                            {"slot": slot, "tag_uid": tag_uid, "reason": "DUPLICATE_UID", "payload": {"matches": mapped_ids, "candidate_ids": mapped_ids}}
                        )
                        self._notify_conflict(slot, tag_uid, tray_meta, mapped_ids, "DUPLICATE_UID")
                        self._record_no_write(slot, "conflict_multiple_bound_matches", {"matches": mapped_ids})
                else:
                    # RFID no eligible match (Shelf/AMS*) => NEEDS_ACTION + notify; do NOT create, do NOT metadata match
                    self.log(
                        f"RFID_MATCH_DEBUG slot={slot} "
                        f"slot_tag={slot_tag} "
                        f"candidates={[{'id': s.get('id'), 'loc': s.get('location'), 'raw': (s.get('extra') or {}).get('rfid_tag_uid'), 'norm': _normalize_rfid_tag_uid((s.get('extra') or {}).get('rfid_tag_uid'))} for s in spools[:10]]}",
                        level="DEBUG",
                    )
                    status = STATUS_UNBOUND_ACTION_REQUIRED
                    unbound += 1
                    t["decision"], t["reason"], t["action"] = "UNBOUND", "ACTION_REQUIRED", "unbound_rfid_no_eligible_match"
                    t["uid_lookup_count"], t["metadata_candidate_ids"] = 0, []
                    self._active_run["unknown_tags"].append({"slot": slot, "tag_uid": tag_uid, "reason": "rfid_no_eligible_match"})
                    self._record_no_write(slot, "rfid_no_eligible_match", {"tag_uid": tag_uid})
                    self._notify_unbound_rfid_no_shelf(slot, tag_uid, tray_meta)
                    self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                    self._log_slot_status_change(slot, status, tag_uid, 0, tray_meta)
                    t["final_slot_status"] = status
                    self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str)
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    continue


            if status.startswith("UNBOUND"):
                unbound += 1 if status != STATUS_UNBOUND_MANUAL_CREATE else 0
                if status == STATUS_UNBOUND_NO_TAG and not status_only:
                    self._force_location_and_helpers(
                        slot, 0, "", source="unbind", previous_helper_spool_id=previous_helper_spool_id
                    )

            self._set_helper(f"input_text.ams_slot_{slot}_status", status)
            self._log_slot_status_change(slot, status, tag_uid, resolved_spool_id, tray_meta)

            t["final_slot_status"] = status
            if status.startswith("UNBOUND"):
                self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str)
            # Central HA signature convergence: single call site; reason from path (expected_autofix, known_binding, etc.) or "converge".
            fid = int(t.get("final_spool_id") or 0)
            if _should_converge_ha_sig(status_only, status, fid):
                self._converge_ha_sig(slot, fid, tray_meta, spool_index, reason=t.get("converge_reason") or "converge", tag_uid=tag_uid or "", tray_empty=tray_empty, tray_state_str=tray_state_str)
            slot_writes = self._active_run["writes"][writes_before_slot:]
            t["writes_performed"] = []
            for w in slot_writes:
                p = w.get("payload", {})
                k = w.get("kind", "")
                if k == "spoolman_patch":
                    t["writes_performed"].append("spoolman_patch:" + str(p.get("path", "")))
                elif k == "spoolman_post":
                    t["writes_performed"].append("spoolman_post:" + str(p.get("path", "")))
                else:
                    t["writes_performed"].append(k + ":" + str(p.get("entity_id", p.get("path", ""))))
            if not t["decision"]:
                t["decision"] = "OK" if status == STATUS_OK else ("UNBOUND" if status.startswith("UNBOUND") else "CONFLICT")
                t["reason"] = status
                if status == STATUS_UNBOUND_NO_TAG:
                    t["action"] = "unbound_no_tag"
                elif status == STATUS_UNBOUND_MANUAL_CREATE:
                    t["action"] = "unbound_manual_create_required"
                elif status == STATUS_CONFLICT_DUPLICATE_UID:
                    t["action"] = "conflict_duplicate_uid"
                elif status == STATUS_MISMATCH:
                    t["action"] = "conflict_mismatch"
                elif "AMBIGUOUS" in status or status == STATUS_CONFLICT_AMBIGUOUS_METADATA:
                    t["action"] = "conflict_ambiguous"
                elif status == STATUS_OK:
                    t["action"] = "known_uid_bind"
                else:
                    t["action"] = "conflict_ambiguous" if t["decision"] == "CONFLICT" else "unknown"
            self._active_run["validation_transcripts"].append(t)
            if validation_mode:
                self._log_validation_transcript(t)

        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        after_slots = {}
        for slot, entity_id in TRAY_ENTITY_BY_SLOT.items():
            if slot in slots_to_process:
                after_slots[str(slot)] = self._snapshot_slot(slot, entity_id)
        mapping = {}
        for slot in TRAY_ENTITY_BY_SLOT:
            sid = self._safe_int(self.get_state(f"input_text.ams_slot_{slot}_expected_spool_id"), 0)
            mapping[str(slot)] = sid
        self.write_last_mapping_json(reason, mapping)
        summary = {
            "timestamp": timestamp,
            "started": started,
            "reason": reason,
            "slots_examined": slots_to_process,
            "ok": ok,
            "unbound": unbound,
            "conflict": conflict,
            "mismatch": mismatch,
            "writes_performed": self._active_run["writes"],
            "writes_count": len(self._active_run["writes"]),
            "conflicts_detected": self._active_run["conflicts"],
            "unknown_tags": self._active_run["unknown_tags"],
            "auto_registers": self._active_run["auto_registers"],
            "no_write_paths": self._active_run["no_write_paths"],
            "validation_transcripts": self._active_run["validation_transcripts"],
            "before_slots": before_slots if self.debug_logs else {},
            "after_slots": after_slots if self.debug_logs else {},
        }
        self.log(
            f"RFID_RECONCILE_SUMMARY reason={reason} ok={ok} unbound={unbound} conflict={conflict} mismatch={mismatch} timestamp={timestamp}"
        )
        if status_only:
            parts = []
            for tr in self._active_run["validation_transcripts"]:
                s = tr.get("slot")
                st = tr.get("final_slot_status", "")
                reason_part = st.split(":", 1)[1] if ":" in st else ""
                short = f"s{s}={st}" if not reason_part else f"s{s}={st.split(':')[0]}({reason_part})"
                parts.append(short)
            self.log("reconcile_all: " + " ".join(parts))
        if self.debug_logs:
            self.log("RFID_RECONCILE_RUN " + json.dumps(summary, sort_keys=True))
        self._append_evidence(summary)
        if len(self._active_run["writes"]) == 0:
            self._debug("No writes performed in this reconcile run", {"reason": reason})
        self._active_run = None

    def _normalize_location(self, loc):
        """Never write deprecated/legacy location strings to Spoolman. Map AMS2_HT_* / HT1 / HT2 to Shelf."""
        key = (loc or "").strip()
        if not key:
            return ""
        # Deprecated keys (AMS2_HT_Slot1/2) or any legacy pattern → Shelf (slots 5/6 not physical).
        if key in DEPRECATED_LOCATION_TO_CANONICAL:
            self.log(f"RFID_LOCATION_GUARD deprecated key to Shelf: {key!r}", level="WARNING")
            return "Shelf"
        if LEGACY_LOCATION_PATTERN.search(key):
            self.log(f"RFID_LOCATION_GUARD legacy pattern to Shelf: {key!r}", level="WARNING")
            return "Shelf"
        return key

    def _force_location_and_helpers(self, slot, spool_id, tag_uid, source, tray_meta=None, tray_state="", tray_identity=None, previous_helper_spool_id=0, spool_index=None, t=None, tray_empty=False, tray_state_str=""):
        slot_loc = CANONICAL_LOCATION_BY_SLOT.get(slot)
        if not slot_loc:
            return
        slot_loc = self._normalize_location(slot_loc)

        if spool_id > 0 and spool_index is not None:
            helper_spool_obj = spool_index.get(spool_id)
            if not self._truth_guard_slot_patch(slot, t or {}, tray_meta or {}, tag_uid, spool_id, helper_spool_obj, tray_empty, tray_state_str):
                self.log(f"TRUTH_GUARD_FORCE_LOC_BLOCK slot={slot} spool_id={spool_id} source={source}", level="INFO")
                return

        dest_clear = LOCATION_NOT_IN_AMS

        # One spool per location: clear previous occupant from this slot when binding changes or unbind.
        # PHASE_2_5 EOL: if previous spool remaining_weight <= 0, move to Empty (end-of-life).
        if previous_helper_spool_id > 0:
            prev_at_slot = False
            prev_spool = None
            try:
                prev_spool = self._spoolman_get(f"/api/v1/spool/{previous_helper_spool_id}")
                if isinstance(prev_spool, dict):
                    prev_loc = str(prev_spool.get("location", "")).strip()
                    prev_at_slot = prev_loc == slot_loc
            except Exception:
                prev_at_slot = False
            if prev_at_slot:
                rem = self._safe_float(prev_spool.get("remaining_weight"), -1.0) if isinstance(prev_spool, dict) else -1.0
                dest = LOCATION_EMPTY if rem <= 0 else dest_clear
                self._spoolman_patch(f"/api/v1/spool/{previous_helper_spool_id}", {"location": dest})
                self.log(
                    f"CLEAR_PREVIOUS_SLOT_OCCUPANT slot={slot} old={previous_helper_spool_id} new={spool_id} from={slot_loc} to={dest}"
                )

        # Unbind: set helper to 0 and tray_signature to "" then return (no new location write).
        if spool_id == 0:
            self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
            self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
            self._set_helper(f"input_text.ams_slot_{slot}_tray_signature", "")
            return

        desired_location = slot_loc
        all_spools = self._spoolman_get("/api/v1/spool?limit=1000")
        if isinstance(all_spools, dict) and "items" in all_spools:
            all_spools = all_spools.get("items", [])
        current_location = ""
        for row in all_spools:
            row_id = self._safe_int(row.get("id"), 0)
            if row_id == spool_id:
                current_location = str(row.get("location", ""))
            if row_id > 0 and row_id != spool_id and str(row.get("location", "")) == desired_location:
                self._spoolman_patch(f"/api/v1/spool/{row_id}", {"location": dest_clear})
        if current_location != desired_location:
            self._spoolman_patch(f"/api/v1/spool/{spool_id}", {"location": desired_location})
        else:
            self._record_no_write(
                slot,
                "location_already_canonical",
                {"spool_id": spool_id, "current_location": current_location, "desired_location": desired_location},
            )
        self._set_helper(f"input_text.ams_slot_{slot}_spool_id", str(spool_id))
        self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", str(spool_id))
        if tray_identity is not None:
            self._set_helper(f"input_text.ams_slot_{slot}_tray_signature", tray_identity)
        elif tray_meta is not None:
            sig = self._build_tray_signature(tray_meta, tray_state, tag_uid)
            self._set_helper(f"input_text.ams_slot_{slot}_tray_signature", sig)
        if self.debug_logs:
            self.log(
                f"slot={slot} source={source} spool_id={spool_id} tag_uid={tag_uid} "
                f"current_location={current_location} desired_location={desired_location}"
            )

    def _bind_uid_to_spool(self, tag_uid, spool_id, spool_index):
        spool = spool_index.get(spool_id)
        if not spool:
            spool = self._spoolman_get(f"/api/v1/spool/{spool_id}")
        current_uid = self._extract_spool_uid(spool)
        slot_tag = _normalize_rfid_tag_uid(tag_uid)
        if current_uid and current_uid != slot_tag:
            raise RuntimeError(f"sticky binding conflict for spool={spool_id} existing_uid={current_uid} incoming_uid={tag_uid}")
        if current_uid == slot_tag:
            self._record_no_write(spool_id, "binding_already_present", {"spool_id": spool_id, "tag_uid": tag_uid})
            return
        extra = dict(spool.get("extra", {}) or {}) if isinstance(spool.get("extra"), dict) else {}
        extra.pop("rfid_uid", None)
        if not extra.get("ha_spool_uuid"):
            extra["ha_spool_uuid"] = str(uuid.uuid4())
        else:
            extra["ha_spool_uuid"] = self._canonicalize_ha_spool_uuid(extra["ha_spool_uuid"])
        existing_rfid = extra.get("rfid_tag_uid")
        if existing_rfid is not None:
            spool_uid_norm = _normalize_rfid_tag_uid(existing_rfid)
            if spool_uid_norm and spool_uid_norm == slot_tag:
                self._record_no_write(spool_id, "binding_already_present", {"spool_id": spool_id, "tag_uid": tag_uid})
                return
        extra["rfid_tag_uid"] = tag_uid
        self._patch_spool_extra_robust(spool_id, extra)

    def _create_spool_from_tray(self, slot, notify=True):
        tray = self.get_state(TRAY_ENTITY_BY_SLOT[slot], attribute="all") or {}
        attrs = tray.get("attributes", {}) if isinstance(tray, dict) else {}
        raw_tag = attrs.get("tag_uid")
        tag_uid = self._canonicalize_tag_uid(raw_tag)
        if not tag_uid:
            raise RuntimeError("tray tag_uid is empty/zero")

        spools = self._spoolman_get("/api/v1/spool?limit=1000")
        if isinstance(spools, dict) and "items" in spools:
            spools = spools.get("items", [])
        slot_tag = _normalize_rfid_tag_uid(tag_uid)
        for row in spools:
            mapped_uid = self._extract_spool_uid(row)
            if mapped_uid == slot_tag:
                raise RuntimeError(f"tag_uid already bound to spool_id={self._safe_int(row.get('id'), 0)}")

        tray_meta = self._tray_meta(attrs, tray.get("state", ""))
        filament_id = self._find_filament_for_tray(tray_meta)
        if filament_id <= 0:
            raise RuntimeError("no deterministic filament match for tray metadata")

        ha_uuid = str(uuid.uuid4())
        enc = _encode_extra_json if _encode_extra_json is not None else json.dumps
        payload = {
            "filament_id": filament_id,
            "location": CANONICAL_LOCATION_BY_SLOT[slot],
            "remaining_weight": self._tray_remaining_weight(attrs),
            "archived": False,
            "comment": f"Created from AMS slot {slot} ({tray_meta.get('name','unknown')})",
            "extra": {
                "ha_spool_uuid": enc(ha_uuid),
                "rfid_tag_uid": enc(tag_uid),
            },
        }
        created = self._spoolman_post("/api/v1/spool", payload)
        created_id = self._safe_int(created.get("id"), 0)
        if created_id <= 0:
            raise RuntimeError("spool create did not return valid id")
        if notify:
            self._notify(
                "RFID Spool Created",
                f"slot={slot} spool_id={created_id} filament_id={filament_id} tag_uid={tag_uid}",
                notification_id=f"rfid_create_spool_slot_{slot}",
            )
        return created_id

    def _manual_enroll(self, slot, spool_id):
        tray = self.get_state(TRAY_ENTITY_BY_SLOT[slot], attribute="all") or {}
        attrs = tray.get("attributes", {}) if isinstance(tray, dict) else {}
        raw_tag = attrs.get("tag_uid")
        tag_uid = self._canonicalize_tag_uid(raw_tag)
        if not tag_uid:
            raise RuntimeError("tray tag_uid is empty/zero")

        spools = self._spoolman_get("/api/v1/spool?limit=1000")
        if isinstance(spools, dict) and "items" in spools:
            spools = spools.get("items", [])
        target = None
        slot_tag = _normalize_rfid_tag_uid(tag_uid)
        for row in spools:
            row_id = self._safe_int(row.get("id"), 0)
            if row_id == spool_id:
                target = row
            mapped_uid = self._extract_spool_uid(row)
            if mapped_uid == slot_tag and row_id != spool_id:
                raise RuntimeError(f"tag_uid bound to different spool_id={row_id}")
        if not target:
            target = self._spoolman_get(f"/api/v1/spool/{spool_id}")
        existing_uid = self._extract_spool_uid(target)
        if existing_uid and existing_uid != slot_tag:
            raise RuntimeError(f"sticky binding conflict on spool_id={spool_id} existing_uid={existing_uid}")

        extra = dict(target.get("extra", {}) or {}) if isinstance(target.get("extra"), dict) else {}
        extra.pop("rfid_uid", None)
        if not extra.get("ha_spool_uuid"):
            extra["ha_spool_uuid"] = str(uuid.uuid4())
        else:
            extra["ha_spool_uuid"] = self._canonicalize_ha_spool_uuid(extra["ha_spool_uuid"])
        extra["rfid_tag_uid"] = tag_uid
        self._patch_spool_extra_robust(spool_id, extra, location=CANONICAL_LOCATION_BY_SLOT[slot])
        self._notify(
            "RFID Manual Enroll Applied",
            f"slot={slot} spool_id={spool_id} tag_uid={tag_uid}",
            notification_id=f"rfid_manual_enroll_slot_{slot}",
        )

    def _find_filament_for_tray(self, tray_meta):
        filaments = self._spoolman_get("/api/v1/filament?limit=1000")
        if isinstance(filaments, dict) and "items" in filaments:
            filaments = filaments.get("items", [])
        tray_colors = set(tray_meta.get("color_candidates", []))
        matches = []
        for filament in filaments if isinstance(filaments, list) else []:
            vendor = (((filament.get("vendor") or {}).get("name")) or "").strip().lower()
            material = str(filament.get("material", "")).strip().lower()
            color_hex = self._normalize_color(str(filament.get("color_hex", "")))
            if vendor != "bambu lab":
                continue
            if self._material_key(material) != self._material_key(tray_meta.get("type", "")):
                continue
            if color_hex not in tray_colors:
                continue
            matches.append(filament)

        if len(matches) > 1:
            narrowed = []
            tray_name = tray_meta.get("name", "").lower()
            tray_filament_id = tray_meta.get("filament_id", "").lower()
            for filament in matches:
                name = str(filament.get("name", "")).lower()
                ext = str(filament.get("external_id", "")).lower()
                if tray_name and tray_name in name:
                    narrowed.append(filament)
                    continue
                if tray_filament_id and (tray_filament_id in ext or tray_filament_id in name):
                    narrowed.append(filament)
            if len(narrowed) == 1:
                matches = narrowed
        return self._safe_int(matches[0].get("id"), 0) if len(matches) == 1 else 0

    def _find_deterministic_candidates(self, spools, tray_meta, slot):
        tray_colors = set(tray_meta.get("color_candidates", []))
        candidates = []
        excluded_new_ids = []
        bambu_excluded = 0
        for spool in spools:
            spool_id = self._safe_int(spool.get("id"), 0)
            if spool_id <= 0:
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "invalid_spool_id"})
                continue
            if self._extract_spool_uid(spool):
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "already_has_rfid"})
                continue
            location = str(spool.get("location", "")).strip().lower()
            # PHASE_2_5: exclude EOL and New from primary (Shelf) pool.
            if location == LOCATION_EMPTY.lower():
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "location_empty"})
                continue
            if location == "new":
                excluded_new_ids.append(spool_id)
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "location_new"})
                continue
            # Eligible: Shelf or any AMS slot; never New (already excluded above).
            if location != "shelf" and not (location.startswith("ams")):
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "location_not_shelf_unknown"})
                continue

            self.log(
                f"RFID_ELIGIBLE_LOCATION slot={slot} spool_id={spool_id} location={spool.get('location', '')}",
                level="DEBUG",
            )
            if _is_bambu_vendor(spool):
                bambu_excluded += 1
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "vendor_bambu_excluded_nonrfid"})
                continue

            filament = spool.get("filament", {}) if isinstance(spool.get("filament", {}), dict) else {}
            spool_material = self._material_key(filament.get("material", ""))
            tray_material = self._material_key(tray_meta.get("type", ""))
            if not spool_material or spool_material != tray_material:
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "material_mismatch"})
                continue

            spool_color = self._normalize_color(str(filament.get("color_hex", "")))
            if not spool_color or spool_color not in tray_colors:
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "color_mismatch"})
                continue
            candidates.append(spool)
            self._record_decision(slot, "candidate_accept", {"spool_id": spool_id, "reason": "strict_match"})

        if bambu_excluded:
            self.log(f"NON_RFID_FILTER_EXCLUDED_BAMBU={bambu_excluded} slot={slot}", level="INFO")
        if excluded_new_ids:
            self.log(
                f"SPOOL_SELECTION_EXCLUDED_NEW slot={slot} excluded_spool_ids={excluded_new_ids}",
                level="INFO",
            )

        if len(candidates) > 1:
            narrowed = []
            tray_name = tray_meta.get("name", "").lower()
            tray_filament_id = tray_meta.get("filament_id", "").lower()
            for spool in candidates:
                filament = spool.get("filament", {}) if isinstance(spool.get("filament", {}), dict) else {}
                names = [
                    str(filament.get("name", "")).lower(),
                    str(spool.get("comment", "")).lower(),
                    str(filament.get("external_id", "")).lower(),
                ]
                if tray_name and any(tray_name in n for n in names if n):
                    narrowed.append(spool)
                    continue
                if tray_filament_id and any(tray_filament_id in n for n in names if n):
                    narrowed.append(spool)
            if len(narrowed) == 1:
                candidates = narrowed

        ineligible_new_count = len(excluded_new_ids)
        return ([self._safe_int(s.get("id"), 0) for s in candidates], ineligible_new_count)

    def _find_deterministic_candidates_new_only(self, spools, tray_meta, slot):
        """PHASE_2_6: Candidates with location New only (same material/color/vendor/name rules as Shelf). Returns (candidate_ids,)."""
        tray_colors = set(tray_meta.get("color_candidates", []))
        candidates = []
        for spool in spools:
            spool_id = self._safe_int(spool.get("id"), 0)
            if spool_id <= 0:
                continue
            if self._extract_spool_uid(spool):
                continue
            location = str(spool.get("location", "")).strip().lower()
            if location != "new":
                continue
            if _is_bambu_vendor(spool):
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "vendor_bambu_excluded_nonrfid"})
                continue
            filament = spool.get("filament", {}) if isinstance(spool.get("filament", {}), dict) else {}
            spool_material = self._material_key(filament.get("material", ""))
            tray_material = self._material_key(tray_meta.get("type", ""))
            if not spool_material or spool_material != tray_material:
                continue
            spool_color = self._normalize_color(str(filament.get("color_hex", "")))
            if not spool_color or spool_color not in tray_colors:
                continue
            candidates.append(spool)
        if len(candidates) > 1:
            narrowed = []
            tray_name = tray_meta.get("name", "").lower()
            tray_filament_id = tray_meta.get("filament_id", "").lower()
            for spool in candidates:
                filament = spool.get("filament", {}) if isinstance(spool.get("filament", {}), dict) else {}
                names = [
                    str(filament.get("name", "")).lower(),
                    str(spool.get("comment", "")).lower(),
                    str(filament.get("external_id", "")).lower(),
                ]
                if tray_name and any(tray_name in n for n in names if n):
                    narrowed.append(spool)
                    continue
                if tray_filament_id and any(tray_filament_id in n for n in names if n):
                    narrowed.append(spool)
            if len(narrowed) == 1:
                candidates = narrowed
        return [self._safe_int(s.get("id"), 0) for s in candidates]

    def _resolve_color_for_ha_sig(self, tray_meta, slot=None, spool_index=None, expected_spool_id=None, candidate_ids=None):
        """
        Resolve color_hex for HA_SIG with fallbacks. Returns normalized 6-char hex or empty string.
        Priority: 1) tray_meta.color_hex, 2) tray_meta.color normalized, 3) filament.color_hex from
        Spoolman for matched spool, 4) vendor/material/name mapping if exists, 5) fail-closed (empty).
        """
        # 1) tray_meta.color_hex if non-empty
        raw = str(tray_meta.get("color_hex", "")).strip()
        color = self._normalize_color(raw)
        if color:
            return color

        # 2) tray_meta.color (if present) normalized to hex
        raw_color = str(tray_meta.get("color", "")).strip()
        color = self._normalize_color(raw_color)
        if color:
            return color

        # 3) filament.color_hex from Spoolman for matched spool
        spool_id = None
        if expected_spool_id and int(expected_spool_id or 0) > 0:
            spool_id = int(expected_spool_id)
        if spool_id is None and candidate_ids:
            ids = [int(x) for x in candidate_ids if int(x or 0) > 0]
            if len(ids) == 1:
                spool_id = ids[0]
        if spool_id and spool_index:
            spool = spool_index.get(spool_id) if isinstance(spool_index, dict) else None
            if spool and isinstance(spool.get("filament"), dict):
                fh = str(spool["filament"].get("color_hex", "")).strip()
                color = self._normalize_color(fh)
                if color:
                    return color

        # 4) vendor/material/name mapping if exists in app
        color = self._resolve_color_from_vendor_mapping(tray_meta)
        if color:
            return color

        # 5) fail-closed
        return ""

    def _resolve_color_from_vendor_mapping(self, tray_meta):
        """Resolve color from vendor/material/name mapping if present. Returns normalized hex or empty."""
        # No built-in Bambu PLA Basic mapping; placeholder for future extension.
        return ""

    def _compute_ha_sig(self, tray_meta, slot=None, spool_index=None, expected_spool_id=None, candidate_ids=None):
        """
        Compute HA_SIG from tray metadata. Format: HA_SIG=bambu|filament_id=X|type=Y|color_hex=Z.
        Values: lowercase, stripped, color_hex normalized (no leading #). Returns None if any required field missing.
        Optional slot/spool_index/expected_spool_id/candidate_ids enable color fallbacks from Spoolman.
        """
        fid = str(tray_meta.get("filament_id", "")).strip().lower()
        t = str(tray_meta.get("type", "")).strip().lower()
        color = self._resolve_color_for_ha_sig(
            tray_meta, slot=slot, spool_index=spool_index,
            expected_spool_id=expected_spool_id, candidate_ids=candidate_ids,
        )
        if not fid or not t or not color:
            return None
        return f"HA_SIG=bambu|filament_id={fid}|type={t}|color_hex={color}"

    def _converge_ha_sig(self, slot, resolved_spool_id, tray_meta, spool_index, reason="converge", tag_uid="", tray_empty=False, tray_state_str=""):
        """
        Idempotent HA signature convergence: compute ha_sig from tray_meta (no input_text helpers);
        if spool.comment != ha_sig, PATCH comment. Only logs when patch occurs (or DEBUG when sig None).
        Updates spool_index cache when PATCHing so later reads see the new comment.
        """
        ha_sig = self._compute_ha_sig(
            tray_meta,
            slot=slot,
            spool_index=spool_index,
            expected_spool_id=resolved_spool_id,
            candidate_ids=[resolved_spool_id],
        )
        if not ha_sig or not str(ha_sig).strip():
            self.log(
                f"HA_SIG_STAMP_SKIPPED reason=empty_sig slot={slot} spool_id={resolved_spool_id} why=missing_filament_id_or_type_or_color",
                level="DEBUG",
            )
            return
        spool = spool_index.get(resolved_spool_id) or self._spoolman_get(f"/api/v1/spool/{resolved_spool_id}")
        if not isinstance(spool, dict):
            return
        if not self._truth_guard_slot_patch(slot, {}, tray_meta or {}, tag_uid, resolved_spool_id, spool, tray_empty, tray_state_str):
            self.log(f"TRUTH_GUARD_HA_SIG_BLOCK slot={slot} spool_id={resolved_spool_id} reason={reason}", level="INFO")
            return
        comment_now = (spool.get("comment") or "").strip()
        if comment_now == ha_sig:
            return
        self._spoolman_patch(f"/api/v1/spool/{resolved_spool_id}", {"comment": ha_sig})
        if isinstance(spool_index, dict) and resolved_spool_id in spool_index:
            spool_index[resolved_spool_id]["comment"] = ha_sig
        self.log(f"HA_SIG_STAMPED slot={slot} spool_id={resolved_spool_id} ha_sig={ha_sig} reason={reason}")

    def _find_flow_b_candidates(self, spools, ha_sig):
        """
        Flow B: Find spools matching HA_SIG in comment for auto-bind.
        Eligible only if: comment==HA_SIG, _unjson(rfid_tag_uid)=="", _unjson(ha_spool_uuid)!="",
        location is None or does NOT start with "AMS".
        """
        if not ha_sig:
            return []
        candidates = []
        for spool in spools:
            spool_id = self._safe_int(spool.get("id"), 0)
            if spool_id <= 0:
                continue
            comment = str(spool.get("comment", "")).strip()
            if comment != ha_sig:
                continue
            extra = spool.get("extra") if isinstance(spool.get("extra"), dict) else {}
            rfid_decoded = self._unjson(extra.get("rfid_tag_uid") or extra.get("rfid_uid"))
            if rfid_decoded != "":
                continue
            ha_uuid_decoded = self._unjson(extra.get("ha_spool_uuid") or extra.get("ha_uuid"))
            if not ha_uuid_decoded or not ha_uuid_decoded.strip():
                continue
            loc = spool.get("location")
            if loc and str(loc).strip().upper().startswith("AMS"):
                continue
            candidates.append(spool)
        return candidates

    def _log_validation_transcript(self, t):
        """Log compact validation transcript for field verification (single-line JSON for grepability)."""
        self.log("RFID_VALIDATE " + json.dumps(t, sort_keys=True))

    def _notify_conflict(self, slot, tag_uid, tray_meta, candidate_ids, reason_string):
        """Notify user of CONFLICT with full context for debugging."""
        meta = (
            f"name={tray_meta.get('name','')} type={tray_meta.get('type','')} "
            f"color_hex={tray_meta.get('color_hex','')} filament_id={tray_meta.get('filament_id','')}"
        )
        ids_str = ",".join(str(x) for x in (candidate_ids or []))
        summary = f"slot={slot} tag_uid={tag_uid}\n{meta}\ncandidate_ids=[{ids_str}]\nreason={reason_string}"
        self._notify(
            f"RFID CONFLICT Slot {slot}",
            summary,
            notification_id=f"rfid_conflict_slot_{slot}_{reason_string}_{tag_uid}",
        )

    def _notify_unbound_rfid_no_shelf(self, slot, tag_uid, tray_meta):
        """RFID UID has no eligible spool (Shelf/AMS*) => NEEDS_ACTION; do not create."""
        summary = (
            f"slot={slot}\n"
            f"tag_uid={tag_uid}\n"
            f"type={tray_meta.get('type','')}\n"
            f"color_hex={tray_meta.get('color_hex','')}\n"
            f"name={tray_meta.get('name','')}\n\n"
            "No spool at eligible location (Shelf or AMS slot) with this RFID UID. ACTION REQUIRED:\n"
            "1) Run script.p1s_rfid_manual_enroll_tag_to_spool with slot + spool_id (spool at Shelf or AMS), OR\n"
            "2) Create spool in Spoolman and enroll, then press input_button.p1s_rfid_reconcile_now."
        )
        self._notify(
            f"RFID NEEDS_ACTION Slot {slot} (no eligible match)",
            summary,
            notification_id=f"rfid_no_shelf_slot_{slot}_{tag_uid}",
        )

    def _notify_nonrfid_needs_action(self, slot, tray_meta, reason_detail):
        """PHASE_2_6: Non-RFID no unambiguous match => NEEDS_ACTION."""
        summary = (
            f"slot={slot}\n"
            f"type={tray_meta.get('type','')}\n"
            f"color_hex={tray_meta.get('color_hex','')}\n"
            f"name={tray_meta.get('name','')}\n"
            f"filament_id={tray_meta.get('filament_id','')}\n\n"
            f"{reason_detail}\n\n"
            "ACTION REQUIRED: Assign spool via Spoolman (Shelf) or run script with slot + spool_id, then reconcile."
        )
        self._notify(
            f"Non-RFID NEEDS_ACTION Slot {slot}",
            summary,
            notification_id=f"nonrfid_needs_action_slot_{slot}",
        )

    def _notify_nonrfid_new_fallback(self, slot, spool_id, tray_meta):
        """PHASE_2_6: Non-RFID bind used New fallback (no Shelf match)."""
        summary = (
            f"slot={slot} bound to spool_id={spool_id} (location New).\n"
            f"type={tray_meta.get('type','')} color_hex={tray_meta.get('color_hex','')} name={tray_meta.get('name','')}\n\n"
            "No matching spool at Shelf; one unambiguous match at New was used. Spool location updated to AMS slot."
        )
        self._notify(
            f"Non-RFID New fallback Slot {slot}",
            summary,
            notification_id=f"nonrfid_new_fallback_slot_{slot}_{spool_id}",
        )

    def _notify_unbound(self, slot, tag_uid, tray_meta, candidate_ids):
        summary = (
            f"slot={slot}\n"
            f"tag_uid={tag_uid}\n"
            f"type={tray_meta.get('type','')}\n"
            f"color_hex={tray_meta.get('color_hex','')}\n"
            f"name={tray_meta.get('name','')}\n"
            f"filament_id={tray_meta.get('filament_id','')}\n"
            f"deterministic_candidates={','.join(str(x) for x in candidate_ids) if candidate_ids else 'none'}\n\n"
            "No deterministic match. ACTION REQUIRED:\n"
            "1) Run script.p1s_rfid_create_spool_from_tray with this slot, OR\n"
            "2) Run script.p1s_rfid_manual_enroll_tag_to_spool with slot + spool_id,\n"
            "3) Press input_button.p1s_rfid_reconcile_now."
        )
        self._notify(
            f"RFID UNBOUND Slot {slot}",
            summary,
            notification_id=f"rfid_unbound_slot_{slot}_{tag_uid}",
        )

    def _tray_meta(self, attrs, state_value):
        raw_color = str(attrs.get("color", ""))
        color_candidates = self._color_candidates(raw_color)
        return {
            "name": str(attrs.get("name", state_value or "")).strip(),
            "type": str(attrs.get("type", "")).strip(),
            "filament_id": str(attrs.get("filament_id", "")).strip(),
            "color": raw_color.strip(),
            "color_hex": color_candidates[0] if color_candidates else "",
            "color_candidates": color_candidates,
        }

    def _has_tray_uuid(self, attrs):
        """True if attrs has a non-empty tray_uuid (after strip)."""
        raw = (attrs or {}).get("tray_uuid")
        return bool(str(raw or "").strip())

    def _norm_tray_identity_tag(self, tag_uid):
        """Normalize tag_uid for tray identity comparison (uppercased, trimmed). Same as _get_tray_identity tag path."""
        return str(tag_uid or "").strip().replace(" ", "").replace('"', "").upper()

    def _is_all_zero_identity(self, tag_uid, tray_uuid):
        """True when tag_uid and tray_uuid are both empty or all-zero (HT non-RFID sensors)."""
        tag_str = str(tag_uid or "").strip().replace(" ", "").replace('"', "").lower()
        tray_str = str(tray_uuid or "").strip().replace(" ", "").replace("-", "").lower()
        return (not tag_str or tag_str == "0000000000000000") and (
            not tray_str or tray_str == "00000000000000000000000000000000"
        )

    def _get_tray_identity(self, attrs, tag_uid, state_str=""):
        """Tray identity: tray_uuid (non-zero) > tag_uid (non-zero) > NONRFID fingerprint."""
        raw_tray = (attrs or {}).get("tray_uuid")
        tray_str = str(raw_tray or "").strip().replace(" ", "").replace("-", "").upper()
        if tray_str and tray_str != "0" * len(tray_str):
            return tray_str
        tag_str = self._norm_tray_identity_tag(tag_uid)
        if tag_str and tag_str != "0" * len(tag_str):
            return tag_str
        return self._compute_ht_fingerprint(attrs, state_str)

    def _spool_exists(self, spool_id):
        """Return True if Spoolman has this spool (GET returns 200 with id). Per-run cached."""
        key = self._safe_int(spool_id, 0)
        if key <= 0:
            return False
        run = getattr(self, "_active_run", None)
        if isinstance(run, dict) and "spool_exists_cache" in run:
            cache = run["spool_exists_cache"]
            if key in cache:
                return cache[key]
            try:
                r = self._spoolman_get(f"/api/v1/spool/{key}")
                result = isinstance(r, dict) and self._safe_int(r.get("id"), 0) == key
            except Exception:
                result = False
            cache[key] = result
            return result
        try:
            r = self._spoolman_get(f"/api/v1/spool/{spool_id}")
            return isinstance(r, dict) and self._safe_int(r.get("id"), 0) == self._safe_int(spool_id, 0)
        except Exception:
            return False

    def _should_stick(self, slot, current_sig, stored_sig, helper_spool_id):
        """True if same tray and helper has valid spool → do not change spool_id (avoid selection churn)."""
        if not current_sig or current_sig != stored_sig or helper_spool_id <= 0:
            return False
        return self._spool_exists(helper_spool_id)

    def _build_tray_signature(self, tray_meta, state_value, tag_uid):
        """Build canonical tray signature (lower/trim, stable ids). Max 255 chars. Used for slots 1–6."""
        name = (str(tray_meta.get("name", state_value or "") or "").strip()).lower()[:64]
        typ = (str(tray_meta.get("type", "") or "").strip()).lower()[:32]
        fid = (str(tray_meta.get("filament_id", "") or "").strip()).lower()[:32]
        hex_ = (str(tray_meta.get("color_hex", "") or "").strip().replace("#", "").lower())[:16]
        uid = (str(tag_uid or "").strip()).lower()[:64]
        parts = [p for p in [name, typ, fid, hex_, uid] if p]
        return "|".join(parts)[:255]

    def _compute_ht_fingerprint(self, attrs, tray_state_str):
        """NONRFID|TYPE|COLOR|STATE fingerprint for HT trays with all-zero identity."""
        typ = re.sub(r"\s+", " ", str((attrs or {}).get("type", "") or "").strip().upper())[:64]
        color = str((attrs or {}).get("color", "") or "").strip().upper().replace("#", "")[:16]
        state = re.sub(r"\s+", " ", str(tray_state_str or "").strip().upper())[:64]
        return f"NONRFID|{typ}|{color}|{state}"[:255]

    def _is_confident_nonrfid(self, attrs, tray_state_str):
        """True when HT tray attributes are specific enough to auto-match."""
        typ = str((attrs or {}).get("type", "") or "").strip()
        color = str((attrs or {}).get("color", "") or "").strip()
        state = str(tray_state_str or "").strip()
        if not typ or not color or not state:
            return False
        if state.upper().startswith("GENERIC"):
            return False
        return True

    def _check_ht_pending(self, slot, current_fp, stored_sig):
        """Check HT fingerprint pending confirmation. Returns (confirmed: bool, pending: bool).

        Stored format: ``PENDING:<count>:<epoch>:<fingerprint>``
        Uses ``:`` to delimit wrapper fields since fingerprint itself uses ``|``.
        """
        import time
        now = time.time()
        sig_helper = f"input_text.ams_slot_{slot}_tray_signature"

        if stored_sig.startswith("PENDING:"):
            parts = stored_sig.split(":", 3)
            if len(parts) == 4:
                count = int(parts[1]) if parts[1].isdigit() else 1
                try:
                    first_seen = float(parts[2])
                except (ValueError, TypeError):
                    first_seen = now
                pending_fp = parts[3]

                if current_fp == pending_fp:
                    count += 1
                    if count >= 2 or (now - first_seen) >= 10:
                        self._set_helper(sig_helper, current_fp)
                        return True, False
                    self._set_helper(sig_helper, f"PENDING:{count}:{first_seen}:{current_fp}"[:255])
                    return False, True

            self._set_helper(sig_helper, f"PENDING:1:{now}:{current_fp}"[:255])
            return False, True

        if not stored_sig or current_fp == stored_sig:
            return True, False

        self._set_helper(sig_helper, f"PENDING:1:{now}:{current_fp}"[:255])
        return False, True

    def _tray_remaining_weight(self, attrs):
        tray_weight = self._safe_float(attrs.get("tray_weight"), 0.0)
        remain_pct = self._safe_float(attrs.get("remain"), 0.0)
        grams = max(0.0, tray_weight * remain_pct / 100.0)
        return round(grams, 1)

    def _unjson(self, v) -> str:
        """Decode Spoolman JSON-string extra value to plain string. Handles '\"ABC\"' -> 'ABC'. Never compare raw extra.* directly."""
        if v is None:
            return ""
        if not isinstance(v, str):
            return str(v)
        s = v.strip()
        if not s:
            return ""
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            try:
                out = json.loads(s)
                return "" if out is None else str(out)
            except Exception:
                return s
        return s

    def _canonicalize_tag_uid(self, raw) -> str:
        """Canonicalize tray or spool tag_uid for comparison and write. Uses shared canonicalizer when available."""
        if _canon_rfid is not None:
            return _canon_rfid(raw)
        return self._normalize_uid(raw)

    def _canonicalize_ha_spool_uuid(self, raw) -> str:
        """Canonicalize ha_spool_uuid for write. Uses shared canonicalizer when available."""
        if _canon_ha_uuid is not None:
            return _canon_ha_uuid(raw)
        return self._unjson(raw).strip().replace('"', "").replace("\\", "").strip()

    def _json_string_literal(self, value: str) -> str:
        """Write value as JSON-string literal for Spoolman extra (e.g. ABC -> '\"ABC\"')."""
        return json.dumps(value)

    def _patch_spool_extra_robust(self, spool_id: int, extra: dict, location=None):
        """
        PATCH spool extra (and optional location). Spoolman requires extra values as valid JSON
        (JSON-in-string). Canonicalize via canonicalize_extra_scalar; reject double-encoded/unsafe;
        build payload with encode_extra_json_string (single encode).
        """
        path = f"/api/v1/spool/{spool_id}"
        extra = dict(extra)
        if _canonicalize_extra_scalar is not None:
            for key in ("rfid_tag_uid", "ha_spool_uuid"):
                if key not in extra or extra[key] is None:
                    continue
                raw = extra[key]
                if _is_double_encoded is not None and _is_double_encoded(raw):
                    self.log(
                        f"SPOOLMAN_EXTRA_SKIP spool_id={spool_id} reason=double_encoded key={key}",
                        level="WARNING",
                    )
                    return
                clean, err = _canonicalize_extra_scalar(raw, key)
                if err is not None:
                    self.log(
                        f"SPOOLMAN_EXTRA_SKIP spool_id={spool_id} key={key} reason={err}",
                        level="WARNING",
                    )
                    return
                extra[key] = clean
        else:
            for key, canon in (("rfid_tag_uid", self._canonicalize_tag_uid), ("ha_spool_uuid", self._canonicalize_ha_spool_uuid)):
                if key in extra and extra[key] is not None:
                    raw = extra[key]
                    extra[key] = canon(raw)
                    if _is_double_encoded is not None and _is_double_encoded(raw):
                        self.log(
                            f"SPOOLMAN_EXTRA_SKIP spool_id={spool_id} reason=double_encoded key={key}",
                            level="WARNING",
                        )
                        return
        if validate_extra_value_no_quotes is not None:
            for key in ("rfid_tag_uid", "ha_spool_uuid"):
                v = extra.get(key)
                if isinstance(v, str) and ('"' in v or "\\" in v):
                    self.log(
                        f"SPOOLMAN_EXTRA_SKIP spool_id={spool_id} reason=quotes_after_canonicalization key={key} value={v!r}",
                        level="WARNING",
                    )
                    return
        if _encode_extra_json is not None:
            payload_extra = {}
            for k, v in extra.items():
                if k in ("rfid_tag_uid", "ha_spool_uuid") and isinstance(v, str):
                    payload_extra[k] = _encode_extra_json(v)
                else:
                    payload_extra[k] = v
        else:
            payload_extra = {k: json.dumps(v) if k in ("rfid_tag_uid", "ha_spool_uuid") and isinstance(v, str) else v for k, v in extra.items()}
        payload = {"extra": payload_extra}
        if location is not None:
            payload["location"] = self._normalize_location(location)
        self._spoolman_patch(path, payload)

    def _json_text_to_str(self, v) -> str:
        """Parse Spoolman JSON literal (e.g. '\\\"764D...\\\"') to plain string for UID comparison."""
        return self._unjson(v)

    def _norm_uid(self, v) -> str:
        """Normalize UID from Spoolman extra (handles JSON-encoded values)."""
        return self._canonicalize_tag_uid(v)

    def _extract_spool_uid(self, spool):
        """Extract and normalize RFID UID from Spoolman spool extra. Uses _normalize_rfid_tag_uid for comparison (handles JSON-encoded string literal)."""
        extra = spool.get("extra", {}) if isinstance(spool.get("extra", {}), dict) else {}
        raw = extra.get("rfid_tag_uid") or extra.get("rfid_uid")
        return _normalize_rfid_tag_uid(raw)

    def _may_stick_override(self, slot, resolved_spool_id, helper_spool_id, tag_uid, spool_index, current_tray_sig, stored_tray_sig):
        """True iff sticky may set resolved_spool_id = helper_spool_id: same tray, helper valid, and (helper already resolved or helper spool UID == tray tag_uid)."""
        if not self._should_stick(slot, current_tray_sig, stored_tray_sig, helper_spool_id):
            return False
        if helper_spool_id == resolved_spool_id:
            return True
        helper_spool = spool_index.get(helper_spool_id) or self._spoolman_get(f"/api/v1/spool/{helper_spool_id}")
        helper_uid = self._extract_spool_uid(helper_spool) if isinstance(helper_spool, dict) else ""
        slot_tag = _normalize_rfid_tag_uid(tag_uid)
        return bool(helper_uid and helper_uid == slot_tag)

    def _rfid_bind_guard_ok(self, resolved_spool_id, tag_uid, spool_index):
        """True iff we may bind this slot to resolved_spool_id (no tag_uid, or selected spool's UID == tag_uid)."""
        if not tag_uid:
            return True
        spool = spool_index.get(resolved_spool_id) or self._spoolman_get(f"/api/v1/spool/{resolved_spool_id}")
        selected_uid = self._extract_spool_uid(spool) if isinstance(spool, dict) else ""
        slot_tag = _normalize_rfid_tag_uid(tag_uid)
        return selected_uid == slot_tag

    def _apply_rfid_bind_guard_fail(self, slot, t, tray_meta, tag_uid, resolved_spool_id, validation_mode):
        """Set status UNBOUND_ACTION_REQUIRED, unbound_reason UNBOUND_SELECTED_UID_MISMATCH, write helpers, append transcript. Caller must unbound += 1 and continue."""
        status = STATUS_UNBOUND_ACTION_REQUIRED
        t["decision"], t["reason"], t["action"] = "UNBOUND", "SELECTED_UID_MISMATCH", "unbound_selected_uid_mismatch"
        t["final_spool_id"], t["selected_spool_id"] = resolved_spool_id, resolved_spool_id
        t["final_slot_status"] = status
        t["unbound_reason"], t["unbound_detail"] = UNBOUND_SELECTED_UID_MISMATCH, "selected_spool_uid_mismatch"
        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
        self._log_slot_status_change(slot, status, tag_uid, resolved_spool_id, tray_meta)
        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_SELECTED_UID_MISMATCH)
        self._active_run["validation_transcripts"].append(t)
        if validation_mode:
            self._log_validation_transcript(t)

    def _truth_guard_slot_patch(self, slot, t, tray_meta, tag_uid, helper_spool_id, helper_spool_obj, tray_empty, tray_state_str):
        """Return True if slot PATCHes are allowed.  False means truth violation detected:
        helpers/unbound_reason are already set, caller must skip all Spoolman writes and continue."""
        norm_tag = _normalize_rfid_tag_uid(tag_uid)
        rfid_visible = bool(norm_tag and norm_tag != "0000000000000000")

        if rfid_visible and helper_spool_id > 0:
            helper_uid = self._extract_spool_uid(helper_spool_obj) if isinstance(helper_spool_obj, dict) else ""
            if helper_uid and helper_uid != norm_tag:
                self.log(
                    f"TRUTH_GUARD_BLOCK slot={slot} mode=RFID_VISIBLE reason={UNBOUND_HELPER_RFID_MISMATCH} "
                    f"helper={helper_spool_id} helper_uid={helper_uid} tag={norm_tag}",
                    level="INFO",
                )
                self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_HELPER_RFID_MISMATCH)
                t["unbound_reason"] = UNBOUND_HELPER_RFID_MISMATCH
                t["unbound_detail"] = f"helper_uid={helper_uid} tag_uid={norm_tag}"
                self._notify(
                    f"RFID Truth Guard – Slot {slot}",
                    f"Helper spool {helper_spool_id} RFID UID ({helper_uid}) does not match tray tag ({norm_tag}). Helper cleared.",
                    notification_id=f"truth_guard_rfid_mismatch_{slot}",
                )
                return False

        if not rfid_visible and not tray_empty and helper_spool_id > 0 and isinstance(helper_spool_obj, dict):
            filament = helper_spool_obj.get("filament") if isinstance(helper_spool_obj.get("filament"), dict) else {}
            spool_material = str(filament.get("material") or "").strip().upper()
            tray_type = str(tray_meta.get("type") or "").strip().upper() if tray_meta else ""
            if spool_material and tray_type and spool_material != tray_type:
                self.log(
                    f"TRUTH_GUARD_BLOCK slot={slot} mode=IDENTITY_UNAVAILABLE reason={UNBOUND_HELPER_MATERIAL_MISMATCH} "
                    f"helper={helper_spool_id} tray_type={tray_type} spool_mat={spool_material}",
                    level="INFO",
                )
                self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_HELPER_MATERIAL_MISMATCH)
                t["unbound_reason"] = UNBOUND_HELPER_MATERIAL_MISMATCH
                t["unbound_detail"] = f"tray_type={tray_type} spool_material={spool_material}"
                self._notify(
                    f"Material Truth Guard – Slot {slot}",
                    f"Helper spool {helper_spool_id} material ({spool_material}) does not match tray type ({tray_type}). Helper cleared.",
                    notification_id=f"truth_guard_material_mismatch_{slot}",
                )
                return False

        return True

    def _normalize_uid(self, raw):
        value = str(raw or "").strip()
        if not value:
            return ""
        for _ in range(2):
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = value[1:-1]
            else:
                break
        value = str(value or "").strip().replace(" ", "").upper()
        if value in ("", "0000000000000000", "UNKNOWN", "UNAVAILABLE", "NONE"):
            return ""
        return value

    def _normalize_color(self, raw):
        value = str(raw or "").strip().lower().replace("#", "")
        if len(value) >= 8 and re.match(r"^[0-9a-f]{8}$", value):
            return value[:6]
        if len(value) >= 6 and re.match(r"^[0-9a-f]{6}$", value[:6]):
            return value[:6]
        return ""

    def _color_candidates(self, raw):
        value = str(raw or "").strip().lower().replace("#", "")
        out = []
        if len(value) >= 8 and re.match(r"^[0-9a-f]{8}$", value):
            out.append(value[:6])
            out.append(value[2:8])
        elif len(value) >= 6 and re.match(r"^[0-9a-f]{6}$", value[:6]):
            out.append(value[:6])
        dedup = []
        for c in out:
            if c not in dedup:
                dedup.append(c)
        return dedup

    def _material_key(self, raw):
        value = str(raw or "").strip().lower()
        value = re.sub(r"\s+", " ", value)
        return value

    def _is_tray_empty(self, tray_state, attrs):
        """Return True if tray state indicates empty (no filament). Physical slots 1–4 only."""
        return str(tray_state or "").strip().lower() == "empty"

    def _normalize_tray_hex(self, raw):
        """Normalize tray/expected color hex to lowercase 6-char hex for comparison. Returns '' if invalid."""
        s = str(raw or "").strip().lower().replace("#", "")
        if len(s) == 8:
            s = s[:6]
        if len(s) >= 6:
            s = s[:6]
        return s if (s and TRAY_HEX_VALID_PATTERN.match(s)) else ""

    def _is_tray_hex_authoritative(self, tray_hex):
        """Return False if tray_hex is empty or a known non-authoritative value (no color comparison / no COLOR_WARNING)."""
        if not tray_hex:
            return False
        return tray_hex not in TRAY_HEX_NON_AUTHORITATIVE

    def _should_emit_color_warning(self, expected_hex, tray_hex):
        """Emit COLOR_WARNING only when expected_hex set, tray_hex is valid and authoritative, and they differ."""
        if not expected_hex:
            return False
        if not tray_hex or tray_hex in TRAY_HEX_NON_AUTHORITATIVE:
            return False
        if not TRAY_HEX_VALID_PATTERN.match(tray_hex):
            return False
        return tray_hex != expected_hex

    def _maybe_log_color_warning(self, slot, expected_hex, tray_hex):
        """Log COLOR_WARNING when expected_hex != tray_hex and tray_hex is authoritative (idempotent for logging)."""
        if self._should_emit_color_warning(expected_hex, tray_hex):
            self.log(
                f"COLOR_WARNING slot={slot} expected_hex={expected_hex} tray_hex={tray_hex}",
                level="WARNING",
            )

    def _get_rfid_pending_until(self, slot):
        """Return UTC datetime when RFID pending window ends for slot, or None if not set/invalid.
        Reads input_text.ams_slot_{slot}_rfid_pending_until (ISO8601 UTC, e.g. 2026-02-23T23:59:59Z).
        Parses ...Z and ...+00:00; strips optional fractional seconds. Compare with datetime (now_utc < pending_until).
        If helper is missing, log WARNING once per slot and treat as no pending window."""
        entity_id = f"input_text.ams_slot_{slot}_rfid_pending_until"
        raw = self.get_state(entity_id)
        if raw is None:
            if slot not in self._pending_helper_warned:
                self._pending_helper_warned.add(slot)
                self.log(
                    f"RFID pending helper missing: {entity_id}; create via UI (Settings → Helpers). Treating as no pending window.",
                    level="WARNING",
                )
            return None
        if not isinstance(raw, str):
            return None
        raw = str(raw).strip()
        if not raw:
            return None
        # Normalize: remove Z or +00:00, then strip optional fractional seconds (e.g. .123456)
        s = raw.rstrip("Z").replace("+00:00", "").strip()
        if "." in s:
            s = s.split(".")[0]
        if len(s) < 19:
            return None
        s = s[:19]
        try:
            return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=None)
        except ValueError:
            return None

    def _set_rfid_pending_until(self, slot, until_utc):
        """Set RFID pending window end for slot (until_utc: naive UTC datetime).
        Writes ISO8601 UTC to input_text.ams_slot_{slot}_rfid_pending_until."""
        entity_id = f"input_text.ams_slot_{slot}_rfid_pending_until"
        dt_str = until_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._set_helper(entity_id, dt_str)

    def _clear_expected_for_slot(self, slot, reason):
        """Clear sticky expected state for a slot when tray is empty. Idempotent: only writes if not already cleared."""
        prefix = f"input_text.ams_slot_{slot}_"
        expected_spool_entity = f"{prefix}expected_spool_id"
        prior = str(self.get_state(expected_spool_entity) or "").strip() or "0"
        helpers_cleared = (
            (expected_spool_entity, "0"),
            (f"{prefix}tray_signature", ""),
            (f"{prefix}expected_material", ""),
            (f"{prefix}expected_color", ""),
            (f"{prefix}expected_color_hex", ""),
            (f"{prefix}expected_type", ""),
        )
        already_cleared = True
        for entity_id, value in helpers_cleared:
            current = str(self.get_state(entity_id) or "").strip()
            if (value == "0" and current != "0") or (value == "" and current != ""):
                already_cleared = False
                break
        if already_cleared:
            return
        for entity_id, value in helpers_cleared:
            self._set_helper(entity_id, value)
        msg = f"RFID_EMPTY_TRAY_CLEAR slot={slot} reason={reason} prior_expected_spool_id={prior}"
        self.log(msg, level="INFO")
        self._append_evidence_line(msg)

    def _set_helper(self, entity_id, value):
        next_value = "" if value is None else str(value).strip()
        if "unbound_reason" in entity_id:
            self.log(f"_SET_HELPER_ENTER entity_id={entity_id} next_value={next_value}", level="INFO")
        state_raw = self.get_state(entity_id)
        if state_raw is None:
            if "unbound_reason" in entity_id:
                self.log(f"_SET_HELPER_SKIP_MISSING entity_id={entity_id}", level="INFO")
            if entity_id not in self._missing_helper_warned:
                self.log(f"helper {entity_id} missing in HA configuration", level="WARNING")
                self._missing_helper_warned.add(entity_id)
            self._record_no_write(entity_id, "helper_missing_in_ha_configuration", {"entity_id": entity_id})
            return

        current = str(state_raw).strip()
        if current == next_value:
            if "unbound_reason" in entity_id:
                # Bypass equality skip: AppDaemon cache can be stale; always write for unbound_reason.
                self.log(f"_SET_HELPER_BYPASS_EQUAL entity_id={entity_id} cached_cur={current} forcing_write", level="DEBUG")
            else:
                self._record_no_write(entity_id, "helper_already_equal", {"entity_id": entity_id, "value": next_value})
                return

        # Route by entity domain: input_text.* -> input_text/set_value, text.* -> text/set_value.
        if entity_id.startswith("input_text."):
            self.call_service("input_text/set_value", entity_id=entity_id, value=next_value)
            if "unbound_reason" in entity_id:
                self.log(f"_SET_HELPER_WROTE entity_id={entity_id} service=input_text/set_value value={next_value}", level="INFO")
            self._record_write("ha_helper_set", {"entity_id": entity_id, "value": next_value})
            return
        if entity_id.startswith("text."):
            self.call_service("text/set_value", entity_id=entity_id, value=next_value)
            if "unbound_reason" in entity_id:
                self.log(f"_SET_HELPER_WROTE entity_id={entity_id} service=text/set_value value={next_value}", level="INFO")
            self._record_write("ha_helper_set", {"entity_id": entity_id, "value": next_value})
            return
        raise ValueError(f"_set_helper: unsupported entity domain for entity_id={entity_id}")

    _LAST_MAPPING_JSON_MAX = 255

    def write_last_mapping_json(self, reason, mapping):
        """Write compact JSON to input_text.p1s_last_mapping_json. Always <= 255 chars."""
        ts = datetime.datetime.now().isoformat()[:19]
        out = json.dumps({"ts": ts, "reason": reason, "mapping": mapping}, separators=(",", ":"))
        if len(out) <= self._LAST_MAPPING_JSON_MAX:
            self._set_helper("input_text.p1s_last_mapping_json", out)
            return
        out = json.dumps({"reason": reason[:32], "mapping": mapping}, separators=(",", ":"))
        if len(out) <= self._LAST_MAPPING_JSON_MAX:
            self._set_helper("input_text.p1s_last_mapping_json", out)
            return
        out = json.dumps({"mapping": mapping}, separators=(",", ":"))
        if len(out) <= self._LAST_MAPPING_JSON_MAX:
            self._set_helper("input_text.p1s_last_mapping_json", out)
            return
        self._set_helper("input_text.p1s_last_mapping_json", out[:self._LAST_MAPPING_JSON_MAX])

    def _apply_unbound_reason(self, slot, t, tray_meta, tag_uid, tray_empty, tray_state_str):
        """Set t[\"unbound_reason\"] and t[\"unbound_detail\"], log one INFO line, and write reason to helper."""
        reason, detail = _classify_unbound_reason(
            tray_meta,
            tag_uid or "",
            t.get("metadata_candidate_ids", []) or [],
            t.get("ineligible_new_count", 0),
            tray_empty=tray_empty,
            tray_state_str=tray_state_str or "",
            raw_tag_uid=t.get("raw_tag_uid"),
        )
        t["unbound_reason"] = reason
        t["unbound_detail"] = detail
        self.log(
            f"UNBOUND_REASON slot={slot} reason={reason} tag_uid={tag_uid or ''} detail={detail}",
            level="INFO",
        )
        entity_id = f"input_text.ams_slot_{slot}_unbound_reason"
        self.log(f"UNBOUND_HELPER_WRITE_ATTEMPT slot={slot} entity_id={entity_id} value={reason}", level="INFO")
        self._set_helper(entity_id, reason)

    def _log_slot_status_change(self, slot, status, tag_uid, spool_id, tray_meta):
        prev = self.last_slot_status.get(slot)
        if prev != status:
            self.last_slot_status[slot] = status
            self.log(
                "RFID_SLOT_STATUS slot=%s status=%s tag_uid=%s spool_id=%s type=%s color_hex=%s name=%s"
                % (
                    slot,
                    status,
                    tag_uid,
                    spool_id,
                    tray_meta.get("type", ""),
                    tray_meta.get("color_hex", ""),
                    tray_meta.get("name", ""),
                )
            )

    def _notify(self, title, message, notification_id=None):
        kwargs = {"title": title, "message": message}
        if notification_id:
            kwargs["notification_id"] = notification_id
        self.call_service("persistent_notification/create", **kwargs)

    def _spoolman_get(self, path):
        req = urllib.request.Request(
            urllib.parse.urljoin(self.spoolman_url + "/", path.lstrip("/")),
            method="GET",
            headers={"Content-Type": "application/json"},
        )
        return self._urlopen_json(req)

    def _spoolman_post(self, path, payload):
        req = urllib.request.Request(
            urllib.parse.urljoin(self.spoolman_url + "/", path.lstrip("/")),
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        return self._urlopen_json(req)

    def _spoolman_patch(self, path, payload):
        spool_id = ""
        if "/spool/" in path:
            try:
                spool_id = path.split("/spool/")[-1].split("/")[0].split("?")[0]
            except (IndexError, AttributeError):
                pass
        # Hard guard at PATCH boundary: never send legacy location (AMS2_HT_*, HT1, HT2).
        if "location" in payload:
            payload = dict(payload)
            payload["location"] = self._normalize_location(payload["location"])
        extra_part = payload.get("extra", "")
        self.log(f"PATCH spool={spool_id} extra={extra_part}", level="DEBUG")
        req = urllib.request.Request(
            urllib.parse.urljoin(self.spoolman_url + "/", path.lstrip("/")),
            method="PATCH",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        result = self._urlopen_json(req)
        self._record_write("spoolman_patch", {"path": path, "payload": payload})
        return result

    def _urlopen_json(self, req):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 400 and "Value is not valid JSON" in detail:
                self.log(
                    f"Spoolman 400: Text extra fields must be JSON-encoded. URL={req.full_url} detail={detail}",
                    level="WARNING",
                )
                self._notify(
                    "Spoolman Extra Field Error",
                    "Spoolman rejected: Value is not valid JSON. Text extra fields (ha_spool_uuid, rfid_tag_uid) must be JSON-encoded (e.g. json.dumps(value)).",
                    notification_id="rfid_spoolman_json_extra",
                )
            raise RuntimeError(f"HTTP {exc.code} for {req.full_url}: {detail}")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"URL error for {req.full_url}: {exc}")

    def _safe_int(self, value, default=0):
        try:
            return int(str(value).strip())
        except (ValueError, TypeError, AttributeError):
            return default

    def _safe_float(self, value, default=0.0):
        try:
            return float(str(value).strip())
        except (ValueError, TypeError, AttributeError):
            return default

    def _snapshot_slot(self, slot, entity_id):
        tray = self.get_state(entity_id, attribute="all") or {}
        attrs = tray.get("attributes", {}) if isinstance(tray, dict) else {}
        return {
            "tag_uid": self._normalize_uid(attrs.get("tag_uid")),
            "tray_state": tray.get("state", "") if isinstance(tray, dict) else "",
            "helper_spool_id": str(self.get_state(f"input_text.ams_slot_{slot}_spool_id") or ""),
            "helper_expected_spool_id": str(self.get_state(f"input_text.ams_slot_{slot}_expected_spool_id") or ""),
            "helper_status": str(self.get_state(f"input_text.ams_slot_{slot}_status") or ""),
        }

    def _record_write(self, kind, payload):
        if self._active_run is None:
            return
        self._active_run["writes"].append({"kind": kind, "payload": payload})

    def _record_no_write(self, target, reason, payload=None):
        if self._active_run is None:
            return
        self._active_run["no_write_paths"].append(
            {
                "target": target,
                "target_type": "slot" if isinstance(target, int) else "entity",
                "reason": reason,
                "payload": payload or {},
            }
        )

    def _record_decision(self, slot, decision, payload):
        if self._active_run is None:
            return
        row = {"slot": slot, "decision": decision, "payload": payload}
        self._active_run["decisions"].append(row)
        self._debug("decision", row)

    def _debug(self, message, payload=None):
        if not self.debug_logs:
            return
        if payload is None:
            self.log(f"RFID_DEBUG {message}")
        else:
            self.log(f"RFID_DEBUG {message} " + json.dumps(payload, sort_keys=True))

    def _append_evidence(self, summary):
        if not self.evidence_log_enabled:
            return
        try:
            with open(self.evidence_log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(summary, sort_keys=True) + "\n")
        except Exception as exc:
            self.log(f"failed to append evidence log: {exc}", level="ERROR")
            self.evidence_log_enabled = False

    def _append_evidence_line(self, line):
        """Append a single text line to the evidence log (e.g. RFID_EMPTY_TRAY_CLEAR). Same file as _append_evidence."""
        if not self.evidence_log_enabled:
            return
        try:
            with open(self.evidence_log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception as exc:
            self.log(f"failed to append evidence line: {exc}", level="ERROR")
            self.evidence_log_enabled = False

    def _ensure_evidence_path_writable(self):
        configured_path = self.evidence_log_path
        candidates = [
            configured_path,
            "/config/ams_rfid_reconcile_evidence.log",
            "/addon_configs/a0d7b954_appdaemon/apps/ams_rfid_reconcile_evidence.log",
            "/tmp/ams_rfid_reconcile_evidence.log",
        ]
        seen = set()
        unique_candidates = []
        for path in candidates:
            if path not in seen:
                seen.add(path)
                unique_candidates.append(path)

        for candidate in unique_candidates:
            try:
                parent = os.path.dirname(candidate)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(candidate, "a", encoding="utf-8"):
                    pass
                self.evidence_log_path = candidate
                self.evidence_log_enabled = True
                self.log(f"evidence logging enabled at {self.evidence_log_path}", level="INFO")
                return
            except Exception:
                continue
        self.evidence_log_enabled = False
