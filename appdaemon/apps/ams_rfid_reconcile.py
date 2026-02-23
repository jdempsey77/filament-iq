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
"""

import datetime
import json
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
except ImportError:
    _canonicalize_extra_scalar = _canon_rfid = _canon_ha_uuid = _encode_extra_json = _is_double_encoded = validate_extra_value_no_quotes = None  # type: ignore[assignment]


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
        self.evidence_log_path = str(self.args.get("evidence_log_path", "/config/ams_rfid_reconcile_evidence.log"))
        self.evidence_log_enabled = True
        self.last_slot_status = {}
        self.debounce_handle = None
        self.debounce_reasons = []
        self._active_run = None
        self._missing_helper_warned = set()
        self._ensure_evidence_path_writable()

        for slot, entity_id in TRAY_ENTITY_BY_SLOT.items():
            self.listen_state(self._on_tray_state_change, entity_id, attribute="all")
            self.log(f"AMS RFID reconcile listening: slot={slot} entity={entity_id}")

        self.listen_event(self._on_reconcile_event, "bambu_rfid_reconcile_now")
        self.listen_event(self._on_reconcile_all_event, "AMS_RECONCILE_ALL")
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

    def _on_reconcile_all_event(self, event_name, data, kwargs):
        """Reconcile statuses only (no Spoolman writes). Fired by script.reconcile_all_ams_slots."""
        payload = data or {}
        reason = str(payload.get("reason", "manual_ui"))
        printer = str(payload.get("printer", ""))
        ts = str(payload.get("ts", ""))
        self.log(f"AMS_RECONCILE_ALL received reason={reason} printer={printer} ts={ts}")
        self._run_reconcile(reason, status_only=True)

    def _on_tray_state_change(self, entity, attribute, old, new, kwargs):
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

        tag_to_spools = {}
        for spool in spools:
            spool_id = self._safe_int(spool.get("id"), 0)
            if spool_id <= 0:
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
            status = "UNBOUND (no tag)"
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
            }

            # Deterministic invariant: empty tray → clear sticky expected state (physical slots 1–4 only).
            if tray_empty:
                self._clear_expected_for_slot(slot, "tray_empty")

            # Defensive: slot must have a canonical location or we can never persist OK
            if slot not in CANONICAL_LOCATION_BY_SLOT:
                self.log(
                    f"slot={slot} is in TRAY_ENTITY_BY_SLOT but missing from CANONICAL_LOCATION_BY_SLOT; cannot persist (missing_canonical_location)",
                    level="ERROR",
                )
                status = "CONFLICT: missing_canonical_location"
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
                status = "UNBOUND: TRAY_UNAVAILABLE"
                t["decision"], t["reason"], t["action"] = "UNBOUND", "TRAY_UNAVAILABLE", "unbound_tray_unavailable"
                self._record_no_write(slot, "tray_unavailable")
            elif not tag_uid:
                status = "UNBOUND: no_tag"
                t["decision"], t["reason"], t["action"] = "UNBOUND", "no_tag", "unbound_no_tag"
                self._record_no_write(slot, "no_tag_uid")
                # Phase 1 non-RFID: when flag ON and tray is non-RFID present, set NON_RFID_UNREGISTERED and skip RFID unbound handling.
                nonrfid_enabled = (self.get_state("input_boolean.p1s_nonrfid_enabled") or "").strip().lower() == "on"
                if nonrfid_enabled and not tray_empty:
                    raw_tag_uid = attrs.get("tag_uid")
                    raw_tray_uuid = attrs.get("tray_uuid")
                    tag_norm = str(raw_tag_uid or "").strip().replace(" ", "").replace('"', "").lower()
                    tray_uuid_norm = str(raw_tray_uuid or "").strip().replace(" ", "").replace("-", "").lower()
                    empty_attr = attrs.get("empty")
                    if (tag_norm == "0000000000000000" and tray_uuid_norm == "00000000000000000000000000000000" and empty_attr is False):
                        status = "NON_RFID_UNREGISTERED"
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "NON_RFID_PRESENT", "nonrfid_unregistered"
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._record_decision(
                            slot,
                            "nonrfid_unregistered",
                            {"decision": "NON_RFID", "reason": "NON_RFID_PRESENT", "action": "nonrfid_unregistered"},
                        )
                        self._log_slot_status_change(slot, status, tag_uid or "", 0, tray_meta)
                        t["final_slot_status"] = status
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue
            elif tag_uid in duplicate_uids:
                status = "CONFLICT: DUPLICATE_UID"
                conflict += 1
                dup_ids = list(set(tag_to_spools.get(tag_uid, [])))
                t["decision"], t["reason"], t["action"] = "CONFLICT", "DUPLICATE_UID", "conflict_duplicate_uid"
                t["uid_lookup_count"], t["metadata_candidate_ids"] = len(dup_ids), dup_ids
                self._active_run["conflicts"].append(
                    {
                        "slot": slot,
                        "tag_uid": tag_uid,
                        "reason": "DUPLICATE_UID",
                        "payload": {"duplicate_uid": tag_uid, "candidate_ids": dup_ids},
                    }
                )
                self._notify_conflict(slot, tag_uid, tray_meta, dup_ids, "DUPLICATE_UID")
                self._record_no_write(slot, "conflict_duplicate_uid")
                # All slots: write tray_signature when tray has data (pure function of tray).
                if tray_state_str not in ("unknown", "unavailable", "", "empty"):
                    sig = self._build_tray_signature(tray_meta, tray.get("state", ""), tag_uid)
                    self._set_helper(f"input_text.ams_slot_{slot}_tray_signature", sig)
                    if slot in (5, 6):
                        self.log(
                            f"RECONCILE_SIG slot={slot} tray_entity={entity_id} tag_uid={tag_uid} "
                            f"filament_id={tray_meta.get('filament_id','')} sig={sig[:80]}{'...' if len(sig) > 80 else ''} "
                            f"wrote=true reason=duplicate_uid",
                            level="DEBUG",
                        )
            else:
                # All slots: write tray_signature when tray has data (pure function of tray).
                # Fixes: tray_signature was only written on successful bind; MISMATCH/ambiguous/etc never wrote it.
                if tray_state_str not in ("unknown", "unavailable", "", "empty"):
                    sig = self._build_tray_signature(tray_meta, tray.get("state", ""), tag_uid)
                    self._set_helper(f"input_text.ams_slot_{slot}_tray_signature", sig)
                    if slot in (5, 6):
                        self.log(
                            f"RECONCILE_SIG slot={slot} tray_entity={entity_id} tag_uid={tag_uid} "
                            f"filament_id={tray_meta.get('filament_id','')} sig={sig[:80]}{'...' if len(sig) > 80 else ''} "
                            f"wrote=true reason=tray_data_present",
                            level="DEBUG",
                        )
                mapped_ids = list(set(tag_to_spools.get(tag_uid, [])))
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
                        color_mismatch = (spool_color != "" and tray_hex != spool_color)
                    mismatch_detected = (expected_vs_resolved_mismatch or color_mismatch) if uid_matched else False
                    if uid_matched and mismatch_detected:
                        if expected_vs_resolved_mismatch and not color_mismatch:
                            # Auto-heal: expected_spool_id is stale, UID says resolved. Trust RFID.
                            if not status_only:
                                self._force_location_and_helpers(
                                    slot, resolved_spool_id, tag_uid, source="expected_autofix",
                                    tray_meta=tray_meta, tray_state=tray.get("state", ""),
                                )
                                self._stamp_ha_sig_if_needed(
                                    slot, resolved_spool_id, tray_meta, spool_index, expected_spool_id=expected_spool_id
                                )
                            status = "OK: FIXED_EXPECTED"
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
                            status = "CONFLICT: MISMATCH"
                            mismatch += 1
                            t["decision"], t["reason"], t["action"] = "CONFLICT", "MISMATCH", "conflict_mismatch"
                            t["uid_lookup_count"] = 1
                            self._record_no_write(
                                slot,
                                "mismatch_expected_or_color",
                                {"expected_spool_id": expected_spool_id, "resolved_spool_id": resolved_spool_id, "tray_hex": tray_hex, "spool_color": spool_color},
                            )
                    elif uid_matched and not mismatch_detected:
                        if not status_only:
                            self._force_location_and_helpers(
                                slot, resolved_spool_id, tag_uid, source="known_binding",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""),
                            )
                            self._stamp_ha_sig_if_needed(
                                slot, resolved_spool_id, tray_meta, spool_index, expected_spool_id=expected_spool_id
                            )
                        status = "OK"
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
                    status = "CONFLICT: DUPLICATE_UID"
                    conflict += 1
                    t["decision"], t["reason"], t["action"] = "CONFLICT", "DUPLICATE_UID", "conflict_duplicate_uid"
                    t["uid_lookup_count"], t["metadata_candidate_ids"] = len(mapped_ids), mapped_ids
                    self._active_run["conflicts"].append(
                        {
                            "slot": slot,
                            "tag_uid": tag_uid,
                            "reason": "DUPLICATE_UID",
                            "payload": {"matches": mapped_ids, "candidate_ids": mapped_ids},
                        }
                    )
                    self._notify_conflict(slot, tag_uid, tray_meta, mapped_ids, "DUPLICATE_UID")
                    self._record_no_write(slot, "conflict_multiple_bound_matches", {"matches": mapped_ids})
                else:
                    candidate_ids = self._find_deterministic_candidates(spools, tray_meta, slot=slot)
                    if len(candidate_ids) == 1:
                        resolved_spool_id = candidate_ids[0]
                        t["uid_lookup_count"], t["metadata_candidate_ids"] = 0, candidate_ids
                        t["decision"], t["reason"], t["action"] = "OK", "auto_register_metadata_match", "auto_register_metadata_match"
                        t["final_spool_id"], t["selected_spool_id"], t["final_location"] = resolved_spool_id, resolved_spool_id, CANONICAL_LOCATION_BY_SLOT[slot]
                        if not status_only:
                            self._bind_uid_to_spool(tag_uid, resolved_spool_id, spool_index)
                            self._active_run["auto_registers"].append(
                                {"kind": "AUTO_REGISTER_RFID_METADATA_MATCH", "slot": slot, "tag_uid": tag_uid, "spool_id": resolved_spool_id}
                            )
                            self._force_location_and_helpers(
                                slot, resolved_spool_id, tag_uid, source="auto_register_metadata_match",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""),
                            )
                            self._stamp_ha_sig_if_needed(
                                slot, resolved_spool_id, tray_meta, spool_index, candidate_ids=candidate_ids
                            )
                        status = "OK"
                        ok += 1
                        self._record_decision(
                            slot,
                            "auto_enroll_a2",
                            {"tag_uid": tag_uid, "resolved_spool_id": resolved_spool_id, "result": "ok"},
                        )
                    elif len(candidate_ids) > 1:
                        # Build full candidate list for tie-break and structured debug
                        candidate_spool_dicts = []
                        for sid in candidate_ids:
                            spool = spool_index.get(sid) or self._spoolman_get(f"/api/v1/spool/{sid}")
                            spool_g = self._safe_float(spool.get("remaining_weight"), -1.0)
                            if spool_g <= 0:
                                continue
                            candidate_spool_dicts.append(spool)

                        match_key = {
                            "slot": slot,
                            "tag_uid": tag_uid,
                            "tray_color_hex": tray_meta.get("color_hex", ""),
                            "tray_material": tray_meta.get("type", ""),
                            "tray_filament_id": tray_meta.get("filament_id", ""),
                        }
                        candidate_list_debug = []
                        for s in candidate_spool_dicts:
                            sid = self._safe_int(s.get("id"), 0)
                            extra = s.get("extra") or {}
                            if not isinstance(extra, dict):
                                extra = {}
                            candidate_list_debug.append({
                                "spool_id": sid,
                                "remaining_weight": self._safe_float(s.get("remaining_weight"), None),
                                "initial_weight": self._safe_float(s.get("initial_weight"), None),
                                "location": str(s.get("location", "")),
                                "archived": bool(s.get("archived", False)),
                                "comment": (str(s.get("comment", "")) or "")[:200],
                                "rfid_tag_uid": "(set)" if self._extract_spool_uid(s) else "",
                            })
                        self.log(
                            f"SPOOL_SELECTION slot={slot} tag_uid={tag_uid} multiple_candidates={len(candidate_spool_dicts)} "
                            f"match_key={match_key} candidates={candidate_list_debug}",
                            level="INFO",
                        )
                        self._active_run.setdefault("selection_debug", []).append({
                            "slot": slot,
                            "tag_uid": tag_uid,
                            "match_key": match_key,
                            "candidates": candidate_list_debug,
                        })

                        strict_mode = getattr(self, "strict_mode_reregister", False)
                        winner_id, tiebreak_reason = tiebreak_choose_spool(candidate_spool_dicts, strict_mode=strict_mode)
                        candidate_weights = {str(self._safe_int(s.get("id"), 0)): float(self._safe_float(s.get("remaining_weight"), -1)) for s in candidate_spool_dicts}

                        if tiebreak_reason == "STRICT_MODE_MULTIPLE_CANDIDATES":
                            self.log(
                                f"SPOOL_SELECTION slot={slot} tag_uid={tag_uid} strict_mode=1 result=REFUSE chosen_id=none reason={tiebreak_reason}",
                                level="INFO",
                            )
                            winner_id = None
                            _source = None
                        else:
                            self.log(
                                f"SPOOL_SELECTION slot={slot} tag_uid={tag_uid} chosen_id={winner_id} reason={tiebreak_reason} candidates={list(candidate_weights.keys())}",
                                level="INFO",
                            )
                            _source = f"auto_enroll_{tiebreak_reason}" if winner_id else None
                            if winner_id is not None:
                                self._active_run["auto_registers"].append({
                                    "kind": "AUTO_REGISTER_RFID_METADATA_TIEBREAK",
                                    "slot": slot,
                                    "tag_uid": tag_uid,
                                    "candidate_weights": candidate_weights,
                                    "chosen_id": winner_id,
                                    "rationale": tiebreak_reason,
                                })
                                self._active_run["auto_registers"].append({
                                    "kind": "UNREGISTERED_TIEBREAK_APPLIED",
                                    "slot": slot,
                                    "tag_uid": tag_uid,
                                    "chosen_id": winner_id,
                                    "rationale": tiebreak_reason,
                                })
                                if self.debug_logs:
                                    self._notify(
                                        "RFID Tie-break Applied",
                                        f"slot={slot} tag_uid={tag_uid} chosen_id={winner_id} reason={tiebreak_reason}",
                                        notification_id=f"rfid_tiebreak_slot_{slot}_{tag_uid}",
                                    )

                        if winner_id is not None:
                            resolved_spool_id = winner_id
                            t["uid_lookup_count"], t["metadata_candidate_ids"] = 0, candidate_ids
                            t["candidate_weights"] = candidate_weights
                            t["decision"], t["reason"], t["action"] = "OK", _source, "auto_register_unregistered_preferred"
                            t["final_spool_id"], t["selected_spool_id"], t["final_location"] = winner_id, winner_id, CANONICAL_LOCATION_BY_SLOT[slot]
                            if not status_only:
                                self._bind_uid_to_spool(tag_uid, resolved_spool_id, spool_index)
                                self._force_location_and_helpers(
                                    slot, resolved_spool_id, tag_uid, source=_source,
                                    tray_meta=tray_meta, tray_state=tray.get("state", ""),
                                )
                                self._stamp_ha_sig_if_needed(
                                    slot, resolved_spool_id, tray_meta, spool_index, candidate_ids=candidate_ids
                                )
                            status = "OK"
                            ok += 1
                            self._record_decision(
                                slot,
                                _source,
                                {"tag_uid": tag_uid, "resolved_spool_id": resolved_spool_id, "result": "ok"},
                            )
                        else:
                            conflict_reason = tiebreak_reason
                            status = f"CONFLICT: {conflict_reason}"
                            conflict += 1
                            t["uid_lookup_count"], t["metadata_candidate_ids"] = 0, candidate_ids
                            t["candidate_weights"] = candidate_weights
                            t["decision"], t["reason"], t["action"] = "CONFLICT", conflict_reason, "conflict_ambiguous"
                            self._active_run["conflicts"].append(
                                {
                                    "slot": slot,
                                    "tag_uid": tag_uid,
                                    "reason": conflict_reason,
                                    "payload": {"candidate_ids": candidate_ids, "candidate_weights": candidate_weights},
                                }
                            )
                            self._active_run["auto_registers"].append(
                                {
                                    "kind": "CONFLICT_MULTIPLE_METADATA_MATCH",
                                    "slot": slot,
                                    "tag_uid": tag_uid,
                                    "candidate_ids": candidate_ids,
                                    "candidate_weights": candidate_weights,
                                    "tiebreak_reason": conflict_reason,
                                }
                            )
                            self._notify_conflict(
                                slot, tag_uid, tray_meta, candidate_ids, conflict_reason
                            )
                            self._record_no_write(
                                slot, "conflict_multiple_metadata_match", {"candidate_ids": candidate_ids, "candidate_weights": candidate_weights, "reason": conflict_reason}
                            )
                    else:
                        # Flow B: HA_SIG auto-bind (comment match). Flow A = deterministic metadata match; Flow B = pre-created spool with HA_SIG.
                        exp_spool = self._safe_int(self.get_state(f"input_text.ams_slot_{slot}_expected_spool_id"), 0)
                        ha_sig = self._compute_ha_sig(
                            tray_meta,
                            slot=slot,
                            spool_index=spool_index,
                            expected_spool_id=exp_spool if exp_spool > 0 else None,
                            candidate_ids=candidate_ids or [],
                        )
                        flow_b_candidates = self._find_flow_b_candidates(spools, ha_sig) if ha_sig else []

                        if len(flow_b_candidates) == 1:
                            resolved_spool_id = self._safe_int(flow_b_candidates[0].get("id"), 0)
                            if resolved_spool_id > 0 and not status_only:
                                self._bind_uid_to_spool(tag_uid, resolved_spool_id, spool_index)
                                try:
                                    self._force_location_and_helpers(
                                        slot, resolved_spool_id, tag_uid, source="flow_b_ha_sig",
                                        tray_meta=tray_meta, tray_state=tray.get("state", ""),
                                    )
                                except Exception as loc_exc:
                                    self.log(
                                        f"FLOW_B_PARTIAL slot={slot} tag_uid={tag_uid} spool_id={resolved_spool_id} "
                                        f"ha_sig={ha_sig} error={loc_exc}",
                                        level="WARNING",
                                    )
                                    status = "UNBOUND: FLOW_B_PARTIAL"
                                    t["decision"], t["reason"], t["action"] = "UNBOUND", "FLOW_B_PARTIAL", "flow_b_partial"
                                    t["uid_lookup_count"], t["metadata_candidate_ids"] = 0, [resolved_spool_id]
                                    self._active_run["unknown_tags"].append({
                                        "slot": slot, "tag_uid": tag_uid, "reason": "flow_b_partial",
                                        "spool_id": resolved_spool_id, "ha_sig": ha_sig,
                                    })
                                    self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                                    self._log_slot_status_change(slot, status, tag_uid, 0, tray_meta)
                                    t["final_slot_status"] = status
                                    unbound += 1
                                    self._active_run["validation_transcripts"].append(t)
                                    if validation_mode:
                                        self._log_validation_transcript(t)
                                    continue
                                self._active_run["auto_registers"].append({
                                    "kind": "FLOW_B_HA_SIG_BOUND",
                                    "slot": slot,
                                    "tag_uid": tag_uid,
                                    "spool_id": resolved_spool_id,
                                    "ha_sig": ha_sig,
                                })
                                self._stamp_ha_sig_if_needed(
                                    slot,
                                    resolved_spool_id,
                                    tray_meta,
                                    spool_index,
                                    expected_spool_id=exp_spool if exp_spool > 0 else None,
                                    candidate_ids=[resolved_spool_id],
                                )
                            status = "OK"
                            ok += 1
                            t["uid_lookup_count"], t["metadata_candidate_ids"] = 0, [resolved_spool_id]
                            t["decision"], t["reason"], t["action"] = "OK", "flow_b_ha_sig", "flow_b_ha_sig_bound"
                            t["final_spool_id"], t["selected_spool_id"], t["final_location"] = (
                                resolved_spool_id, resolved_spool_id, CANONICAL_LOCATION_BY_SLOT[slot]
                            )
                            self._record_decision(
                                slot,
                                "flow_b_ha_sig",
                                {"tag_uid": tag_uid, "resolved_spool_id": resolved_spool_id, "ha_sig": ha_sig, "result": "ok"},
                            )
                            self.log(
                                f"FLOW_B_BOUND slot={slot} tag_uid={tag_uid} spool_id={resolved_spool_id} ha_sig={ha_sig}"
                            )
                        elif len(flow_b_candidates) > 1:
                            cids = [self._safe_int(s.get("id"), 0) for s in flow_b_candidates]
                            self.log(
                                f"FLOW_B_AMBIGUOUS slot={slot} tag_uid={tag_uid} ha_sig={ha_sig} candidates={cids}"
                            )
                            status = "UNBOUND: ACTION_REQUIRED"
                            t["uid_lookup_count"], t["metadata_candidate_ids"] = 0, candidate_ids
                            t["decision"], t["reason"], t["action"] = "UNBOUND", "ACTION_REQUIRED", "unbound_flow_b_ambiguous"
                            self._active_run["unknown_tags"].append({"slot": slot, "tag_uid": tag_uid, "reason": "flow_b_ambiguous", "candidates": cids})
                            self._record_no_write(slot, "flow_b_ambiguous", {"tag_uid": tag_uid, "ha_sig": ha_sig, "candidates": cids})
                            self._notify_unbound(slot, tag_uid, tray_meta, cids)
                        else:
                            if ha_sig:
                                self.log(f"FLOW_B_NONE slot={slot} tag_uid={tag_uid} ha_sig={ha_sig}")
                            filament_id = self._find_filament_for_tray(tray_meta)
                            if filament_id <= 0:
                                status = "UNBOUND: manual_create_required"
                                unbound += 1
                                t["uid_lookup_count"], t["metadata_candidate_ids"] = 0, candidate_ids
                                t["decision"], t["reason"], t["action"] = "UNBOUND", "manual_create_required", "unbound_manual_create_required"
                                self._active_run["unknown_tags"].append({"slot": slot, "tag_uid": tag_uid})
                                self._record_no_write(
                                    slot,
                                    "unknown_tag_no_deterministic_candidate",
                                    {"tag_uid": tag_uid, "candidate_ids": candidate_ids},
                                )
                                self._notify_unbound(slot, tag_uid, tray_meta, candidate_ids)
                            else:
                                status = "UNBOUND: ACTION_REQUIRED"
                                t["uid_lookup_count"], t["metadata_candidate_ids"] = 0, candidate_ids
                                t["decision"], t["reason"], t["action"] = "UNBOUND", "ACTION_REQUIRED", "unbound_action_required"
                                self._active_run["unknown_tags"].append({"slot": slot, "tag_uid": tag_uid, "reason": "no_uid_match_no_create"})
                                self._record_no_write(
                                    slot,
                                    "unmatched_rfid_auto_create_disabled",
                                    {"tag_uid": tag_uid, "filament_id": filament_id},
                                )
                                self.log(
                                    f"UNBOUND: ACTION_REQUIRED slot={slot} tag_uid={tag_uid} "
                                    "match_count=0 auto_create_disabled (manual enroll or Create Spool from Tray required)",
                                    level="WARNING",
                                )
                                self._notify_unbound(slot, tag_uid, tray_meta, candidate_ids or [])

            if status.startswith("UNBOUND"):
                unbound += 1 if status != "UNBOUND: manual_create_required" else 0
                if status == "UNBOUND: no_tag" and not status_only:
                    self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")

            self._set_helper(f"input_text.ams_slot_{slot}_status", status)
            self._log_slot_status_change(slot, status, tag_uid, resolved_spool_id, tray_meta)

            t["final_slot_status"] = status
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
                t["decision"] = "OK" if status == "OK" else ("UNBOUND" if status.startswith("UNBOUND") else "CONFLICT")
                t["reason"] = status
                if status == "UNBOUND: no_tag":
                    t["action"] = "unbound_no_tag"
                elif status == "UNBOUND: manual_create_required":
                    t["action"] = "unbound_manual_create_required"
                elif status == "CONFLICT: DUPLICATE_UID":
                    t["action"] = "conflict_duplicate_uid"
                elif status == "CONFLICT: MISMATCH":
                    t["action"] = "conflict_mismatch"
                elif "AMBIGUOUS" in status or status == "CONFLICT: AMBIGUOUS_METADATA_NO_UNREGISTERED":
                    t["action"] = "conflict_ambiguous"
                elif status == "OK":
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

    def _force_location_and_helpers(self, slot, spool_id, tag_uid, source, tray_meta=None, tray_state=""):
        desired_location = self._normalize_location(CANONICAL_LOCATION_BY_SLOT[slot])
        all_spools = self._spoolman_get("/api/v1/spool?limit=1000")
        if isinstance(all_spools, dict) and "items" in all_spools:
            all_spools = all_spools.get("items", [])
        current_location = ""
        for row in all_spools:
            row_id = self._safe_int(row.get("id"), 0)
            if row_id == spool_id:
                current_location = str(row.get("location", ""))
            if row_id > 0 and row_id != spool_id and str(row.get("location", "")) == desired_location:
                self._spoolman_patch(f"/api/v1/spool/{row_id}", {"location": "Shelf"})
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
        if tray_meta is not None:
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
        if current_uid and current_uid != tag_uid:
            raise RuntimeError(f"sticky binding conflict for spool={spool_id} existing_uid={current_uid} incoming_uid={tag_uid}")
        if current_uid == tag_uid:
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
            spool_uid_norm = self._norm_uid(existing_rfid)
            if spool_uid_norm and spool_uid_norm == tag_uid:
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
        for row in spools:
            mapped_uid = self._extract_spool_uid(row)
            if mapped_uid == tag_uid:
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
        for row in spools:
            row_id = self._safe_int(row.get("id"), 0)
            if row_id == spool_id:
                target = row
            mapped_uid = self._extract_spool_uid(row)
            if mapped_uid == tag_uid and row_id != spool_id:
                raise RuntimeError(f"tag_uid bound to different spool_id={row_id}")
        if not target:
            target = self._spoolman_get(f"/api/v1/spool/{spool_id}")
        existing_uid = self._extract_spool_uid(target)
        if existing_uid and existing_uid != tag_uid:
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
        for spool in spools:
            spool_id = self._safe_int(spool.get("id"), 0)
            if spool_id <= 0:
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "invalid_spool_id"})
                continue
            if self._extract_spool_uid(spool):
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "already_has_rfid"})
                continue
            location = str(spool.get("location", "")).strip().lower()
            # Location "New" = never used; must not be auto-selected. Exclude before tie-break.
            if location == "new":
                excluded_new_ids.append(spool_id)
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "location_new"})
                continue
            if location not in ("", "shelf", "unknown"):
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "location_not_shelf_unknown"})
                continue

            filament = spool.get("filament", {}) if isinstance(spool.get("filament", {}), dict) else {}
            vendor = (((filament.get("vendor") or {}).get("name")) or "").strip().lower()
            if vendor != "bambu lab":
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "vendor_not_bambu"})
                continue

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

    def _stamp_ha_sig_if_needed(
        self,
        slot,
        resolved_spool_id,
        tray_meta,
        spool_index,
        expected_spool_id=None,
        candidate_ids=None,
    ):
        """
        After successful bind: compute HA_SIG; if not None and spool comment != HA_SIG, PATCH comment.
        Idempotent; logs HA_SIG_STAMPED only when comment actually changes.
        """
        ha_sig = self._compute_ha_sig(
            tray_meta,
            slot=slot,
            spool_index=spool_index,
            expected_spool_id=expected_spool_id
            if (expected_spool_id is not None and self._safe_int(expected_spool_id, 0) > 0)
            else (self._safe_int(self.get_state(f"input_text.ams_slot_{slot}_expected_spool_id"), 0) or None),
            candidate_ids=candidate_ids if candidate_ids is not None else [resolved_spool_id],
        )
        if ha_sig is None:
            return
        spool = spool_index.get(resolved_spool_id) or self._spoolman_get(f"/api/v1/spool/{resolved_spool_id}")
        if not isinstance(spool, dict):
            return
        current_comment = (spool.get("comment") or "").strip()
        if current_comment == ha_sig:
            return
        self._spoolman_patch(f"/api/v1/spool/{resolved_spool_id}", {"comment": ha_sig})
        self.log(f"HA_SIG_STAMPED spool_id={resolved_spool_id} ha_sig={ha_sig}")

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

    def _build_tray_signature(self, tray_meta, state_value, tag_uid):
        """Build canonical tray signature (lower/trim, stable ids). Max 255 chars. Used for slots 1–6."""
        name = (str(tray_meta.get("name", state_value or "") or "").strip()).lower()[:64]
        typ = (str(tray_meta.get("type", "") or "").strip()).lower()[:32]
        fid = (str(tray_meta.get("filament_id", "") or "").strip()).lower()[:32]
        hex_ = (str(tray_meta.get("color_hex", "") or "").strip().replace("#", "").lower())[:16]
        uid = (str(tag_uid or "").strip()).lower()[:64]
        parts = [p for p in [name, typ, fid, hex_, uid] if p]
        return "|".join(parts)[:255]

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
        """Extract and normalize RFID UID from Spoolman spool extra. Uses canonicalizer for comparison tolerance."""
        extra = spool.get("extra", {}) if isinstance(spool.get("extra", {}), dict) else {}
        raw = extra.get("rfid_tag_uid") or extra.get("rfid_uid")
        return self._canonicalize_tag_uid(raw)

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
        state_raw = self.get_state(entity_id)
        if state_raw is None:
            if entity_id not in self._missing_helper_warned:
                self.log(f"helper {entity_id} missing in HA configuration", level="WARNING")
                self._missing_helper_warned.add(entity_id)
            self._record_no_write(entity_id, "helper_missing_in_ha_configuration", {"entity_id": entity_id})
            return

        current = str(state_raw).strip()
        next_value = str(value).strip()
        if current == next_value:
            self._record_no_write(entity_id, "helper_already_equal", {"entity_id": entity_id, "value": next_value})
            return
        self.call_service("input_text/set_value", entity_id=entity_id, value=next_value)
        self._record_write("ha_helper_set", {"entity_id": entity_id, "value": next_value})

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
