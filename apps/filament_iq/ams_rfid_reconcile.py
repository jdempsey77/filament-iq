"""
AMS RFID Reconcile — Spec v4: lot_nr identity model.

Identity model (v4):
  RFID spools:     lot_nr = tray_uuid (32-char hex spool serial from RFID chip)
  Non-RFID spools: lot_nr = type|filament_id|color_hex (pipe-delimited sig, lowercase)
  comment field:   free for human use — never written by reconciler
  extra fields:    read-only fallback during migration window, never written

Migration fallback (read-only, will be retired after Legacy Field Cleanup):
  RFID: if lot_nr empty, check extra.rfid_tag_uid via canonicalizer → write tray_uuid to lot_nr
  Non-RFID: if lot_nr empty, check comment for legacy HA_SIG → write sig to lot_nr

Fail-closed behavior:
  Ambiguity (0 or >1 candidates) → UNBOUND. Guard policy unchanged.
"""

import datetime
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import hassapi as hass

from .base import FilamentIQBase, build_slot_mappings

# v4: canonicalizer retired (moved to _retired/). These symbols are set to None so
# existing fallback guards (`if _canon_rfid is not None`) degrade gracefully.
_canonicalize_extra_scalar = None
_canon_ha_uuid = None
_canon_rfid = None
_encode_extra_json = None
_is_double_encoded = None
validate_extra_value_no_quotes = None

try:
    from appdaemon.exceptions import DomainException
except ImportError:
    DomainException = None  # running outside AppDaemon (e.g. unit tests)

# Slot mappings built from config in initialize() — see _tray_entity_by_slot, _canonical_location_by_slot, _physical_ams_slots.

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


def is_generic_filament_id(filament_id: str) -> bool:
    """True when filament_id is a Bambu generic sentinel (ends in 99)."""
    return str(filament_id or "").strip().upper().endswith("99")


def _is_lot_nr_uuid(lot_nr) -> bool:
    """True when lot_nr is a 32-char hex UUID (RFID-enrolled); such spools must not match non-RFID trays."""
    s = str(lot_nr or "").strip()
    return bool(s and re.fullmatch(r"[0-9a-fA-F]{32}", s))


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


def _color_distance(hex1: str, hex2: str) -> float:
    """Euclidean RGB distance between two normalized 6-char hex colors. Returns 999 on parse error."""
    try:
        h1 = _normalize_hex_color(hex1)
        h2 = _normalize_hex_color(hex2)
        if h1 is None or h2 is None:
            return 999.0
        r1, g1, b1 = int(h1[0:2], 16), int(h1[2:4], 16), int(h1[4:6], 16)
        r2, g2, b2 = int(h2[0:2], 16), int(h2[2:4], 16), int(h2[4:6], 16)
        return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5
    except Exception:
        return 999.0


NONRFID_COLOR_TOLERANCE = 50.0


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


class AmsRfidReconcile(FilamentIQBase):
    def initialize(self):
        self._validate_config(
            required_keys=["spoolman_url", "printer_serial"],
            typed_keys={
                "safety_poll_seconds": (int, 600),
                "debounce_seconds": (int, 3),
                "color_distance_threshold": (int, 30),
                "dry_run": (bool, False),
            },
            range_keys={
                "safety_poll_seconds": (1, None),
                "debounce_seconds": (1, None),
                "color_distance_threshold": (0, 255),
            },
        )
        self._check_spoolman_connectivity()

        self.log("ams_rfid_reconcile VERSION=2026-02-18 flow-b-ha-sig", level="INFO")
        self.enabled = bool(self.args.get("enabled", True))
        if not self.enabled:
            self.log("AMS RFID reconcile disabled by config (enabled=false).")
            return

        self._prefix = self._build_entity_prefix()
        ams_units = self.args.get("ams_units")
        (
            self._tray_entity_by_slot,
            self._slot_by_tray_entity,
            _,
            self._canonical_location_by_slot,
        ) = build_slot_mappings(self._prefix, ams_units)
        self._physical_ams_slots = tuple(sorted(self._tray_entity_by_slot.keys()))
        self._last_mapping_json_entity = str(
            self.args.get(
                "last_mapping_json_entity",
                "input_text.filament_iq_last_mapping_json",
            )
        ).strip()

        self.spoolman_url = str(
            self.args.get("spoolman_url", self.args.get("spoolman_base_url", ""))
        ).rstrip("/")
        self.startup_delay_seconds = int(self.args.get("startup_delay_seconds", 60))
        self.startup_wait_helpers_seconds = int(self.args.get("startup_wait_helpers_seconds", 420))
        self.startup_wait_retry_initial_seconds = int(self.args.get("startup_wait_retry_initial_seconds", 2))
        self.startup_wait_retry_max_seconds = int(self.args.get("startup_wait_retry_max_seconds", 30))
        self.startup_probe_helper_entity = str(
            self.args.get("startup_probe_helper_entity", "input_text.ams_slot_1_spool_id")
        )
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
        self._pending_lot_nr_writes = {}
        self._suppress_helper_change_until = {}
        self._missing_helper_warned = set()
        self._pending_helper_warned = set()
        self._domain_exception_class_logged = False
        self._print_active_entity = str(
            self.args.get(
                "print_active_entity",
                "input_boolean.filament_iq_print_active",
            )
        ).strip()
        self._print_active_since = None
        if str(self.get_state(self._print_active_entity) or "").lower() == "on":
            self._print_active_since = time.time()
            self.log("PRINT_ACTIVE_FREEZE_START reason=already_on_at_startup", level="INFO")
        self._ensure_evidence_path_writable()
        if DomainException is not None:
            self.log(
                f"STARTUP_WAIT_EXCEPTION_CLASS={DomainException.__module__}.{DomainException.__name__}",
                level="INFO",
            )

        for slot, entity_id in self._tray_entity_by_slot.items():
            self.listen_state(self._on_tray_state_change, entity_id, attribute="all")
            self.log(f"AMS RFID reconcile listening: slot={slot} entity={entity_id}")

        for slot in self._physical_ams_slots:
            helper_entity = f"input_text.ams_slot_{slot}_spool_id"
            self.listen_state(self._on_helper_spool_id_change, helper_entity)
            self.log(f"AMS RFID reconcile listening helper: slot={slot} entity={helper_entity}")

        self.listen_event(self._on_reconcile_event, "bambu_rfid_reconcile_now")
        self.listen_event(self._on_reconcile_all_event, "AMS_RECONCILE_ALL")
        self._reconcile_button_entity = str(
            self.args.get(
                "reconcile_button_entity",
                "input_button.filament_iq_reconcile_now",
            )
        ).strip()
        self._startup_suppress_entity = str(
            self.args.get(
                "startup_suppress_swap_entity",
                "input_boolean.filament_iq_startup_suppress_swap",
            )
        ).strip()
        self.listen_state(
            self._on_manual_reconcile_button,
            self._reconcile_button_entity,
        )
        self.listen_state(
            self._on_print_active_change,
            self._print_active_entity,
        )
        self.listen_event(self._on_manual_enroll_event, "bambu_rfid_manual_enroll_tag_to_spool")
        self.listen_event(self._on_slot_assigned, "FILAMENT_IQ_SLOT_ASSIGNED")
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

    def _on_print_active_change(self, entity, attribute, old, new, kwargs):
        """Track print-active transitions for reconcile freeze/thaw."""
        new_val = str(new).lower()
        old_val = str(old).lower()
        if new_val == "on":
            self._print_active_since = time.time()
            self.log("PRINT_ACTIVE_FREEZE_START", level="INFO")
        elif new_val == "off" and old_val == "on":
            self._print_active_since = None
            self.log(
                "PRINT_ENDED_RECONCILE_TRIGGERED reason=print_active_off",
                level="INFO",
            )
            self._schedule_reconcile("print_ended")

    def _run_reconcile_startup(self, kwargs):
        # Suppress HA spool-swap detection for 90s after startup (avoids false positives from bulk helper updates).
        try:
            self.call_service(
                "input_boolean/turn_on",
                entity_id=self._startup_suppress_entity,
            )
        except Exception as e:
            self.log("Failed to set filament_iq_startup_suppress_swap on: %s" % (e,), level="WARNING")
        if DomainException is None:
            self._clear_legacy_signatures()
            self._run_reconcile("startup_delay")
            return
        budget_sec = self.startup_wait_helpers_seconds
        end_utc = kwargs.get("_readiness_end_utc")
        next_interval_sec = kwargs.get("_readiness_next_interval_sec")
        if end_utc is None:
            end_utc = datetime.datetime.utcnow() + datetime.timedelta(seconds=budget_sec)
            next_interval_sec = self.startup_wait_retry_initial_seconds
        probe_entity = self.startup_probe_helper_entity
        now = datetime.datetime.utcnow()
        if now >= end_utc:
            self.log(
                "STARTUP_WAIT_TIMEOUT startup helpers not ready after {}s — press manual reconcile once HA is stable".format(
                    budget_sec
                ),
                level="ERROR",
            )
            return
        reason = None
        try:
            full = self.get_state(probe_entity, attribute="all")
            if full is None:
                reason = "helper_unavailable"
            else:
                state_val = full.get("state") if isinstance(full, dict) else None
                if state_val is None:
                    reason = "helper_unavailable"
                elif str(state_val).lower() == "unavailable":
                    reason = "helper_unavailable"
                elif full.get("attributes", {}).get("restored") is True:
                    reason = "helper_restored"
        except DomainException as e:
            if not self._domain_exception_class_logged:
                self.log(
                    f"domain not available (first occurrence) exception_class={type(e).__module__}.{type(e).__name__}",
                    level="WARNING",
                )
                self._domain_exception_class_logged = True
            reason = "domain_not_available"
        if reason is not None:
            remaining = max(0, (end_utc - now).total_seconds())
            self.log(
                "STARTUP_WAIT_HELPERS_NOT_READY remaining_seconds={:.0f} reason={}".format(remaining, reason),
                level="WARNING",
            )
            delay = min(next_interval_sec, self.startup_wait_retry_max_seconds)
            self.run_in(
                self._run_reconcile_startup,
                delay,
                _readiness_end_utc=end_utc,
                _readiness_next_interval_sec=min(delay * 2, self.startup_wait_retry_max_seconds),
            )
            return
        self.log("STARTUP_WAIT_HELPERS_READY", level="INFO")
        # Validate get_state vs get_state(attribute='all') for debugging
        for _dbg_slot in range(1, 7):
            _dbg_eid = f"input_text.ams_slot_{_dbg_slot}_spool_id"
            _dbg_plain = self.get_state(_dbg_eid)
            _dbg_full = self.get_state(_dbg_eid, attribute="all")
            _dbg_safe = _dbg_full.get("state") if isinstance(_dbg_full, dict) else _dbg_full
            if str(_dbg_plain) != str(_dbg_safe):
                self.log(
                    f"STARTUP_STATE_MISMATCH slot={_dbg_slot} plain={_dbg_plain!r} full={_dbg_safe!r}",
                    level="WARNING",
                )
            else:
                self.log(
                    f"STARTUP_STATE_OK slot={_dbg_slot} value={_dbg_plain!r}",
                    level="INFO",
                )
        self._clear_legacy_signatures()
        self._run_reconcile("startup_delay")

    def _run_reconcile_poll(self, kwargs):
        self._run_reconcile("safety_poll", status_only=True)

    def _on_reconcile_event(self, event_name, data, kwargs):
        reason = str((data or {}).get("reason", "ui_button"))
        self._schedule_reconcile(reason)

    def _on_manual_reconcile_button(self, entity, attribute, old, new, kwargs):
        """Trigger full reconcile when reconcile button state (ISO timestamp) changes."""
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
        slot = next((s for s, e in self._tray_entity_by_slot.items() if e == entity), None)
        if slot is not None:
            until_utc = datetime.datetime.utcnow() + datetime.timedelta(seconds=RFID_PENDING_SECONDS)
            self._set_rfid_pending_until(slot, until_utc)
        self._schedule_reconcile(f"tray_update:{entity}")

    def _on_helper_spool_id_change(self, entity, attribute, old, new, kwargs):
        new_val = self._safe_int(new, 0)
        old_val = self._safe_int(old, 0)
        if new_val == old_val:
            return
        slot = next(
            (s for s in self._physical_ams_slots if f"input_text.ams_slot_{s}_spool_id" == entity),
            None,
        )
        if slot is None:
            return
        if self._active_run is not None:
            return
        suppress_until = self._suppress_helper_change_until.get(slot)
        if suppress_until and datetime.datetime.utcnow() < suppress_until:
            self.log(f"HELPER_SPOOL_ID_CHANGE_SUPPRESSED slot={slot} old={old_val} new={new_val}", level="DEBUG")
            return
        self.log(f"HELPER_SPOOL_ID_CHANGED slot={slot} old={old_val} new={new_val}", level="INFO")
        self._schedule_reconcile(f"helper_change_slot_{slot}")

    def _on_manual_enroll_event(self, event_name, data, kwargs):
        payload = data or {}
        slot = self._safe_int(payload.get("slot"), 0)
        spool_id = self._safe_int(payload.get("spool_id"), 0)
        if slot not in self._tray_entity_by_slot or spool_id <= 0:
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

    def _read_tray_color_hex(self, slot):
        """Read AMS tray color for a slot. Returns 6-char uppercase hex (e.g. '161616') or None."""
        entity_id = self._tray_entity_by_slot.get(slot)
        if not entity_id:
            return None
        tray = self.get_state(entity_id, attribute="all") or {}
        attrs = tray.get("attributes", {}) if isinstance(tray, dict) else {}
        raw = str(attrs.get("color", "") or "").strip()
        if not raw:
            return None
        raw = raw.lstrip("#")
        if len(raw) == 8:
            raw = raw[:6]
        if len(raw) != 6:
            return None
        return raw.upper()

    def _sync_filament_color_on_bind(self, slot, spool_id, sync_mode):
        """Sync Spoolman filament color_hex to match AMS tray color on manual bind.
        Returns True if PATCHed, False if skipped/failed."""
        if not sync_mode:
            return False
        # Resolve target color
        if sync_mode == "auto":
            target_color = self._read_tray_color_hex(slot)
            if not target_color:
                self.log(
                    f"SYNC_COLOR_NO_TRAY_COLOR slot={slot} spool_id={spool_id}",
                    level="WARNING",
                )
                return False
        elif len(sync_mode) == 6 and all(c in "0123456789abcdefABCDEF" for c in sync_mode):
            target_color = sync_mode.upper()
        else:
            self.log(
                f"SYNC_COLOR_INVALID_MODE slot={slot} spool_id={spool_id} mode={sync_mode}",
                level="WARNING",
            )
            return False

        # Get spool and filament info from Spoolman
        spool = self._spoolman_get(f"/api/v1/spool/{spool_id}")
        if not isinstance(spool, dict) or "filament" not in spool:
            self.log(
                f"SYNC_COLOR_SPOOL_NOT_FOUND slot={slot} spool_id={spool_id}",
                level="WARNING",
            )
            return False
        filament = spool["filament"]
        filament_id = filament.get("id")
        existing_color = str(filament.get("color_hex") or "").strip().lstrip("#").upper()
        if len(existing_color) == 8:
            existing_color = existing_color[:6]

        if existing_color == target_color:
            self.log(
                f"SYNC_COLOR_ALREADY_MATCHES filament_id={filament_id} color={target_color} spool_id={spool_id} slot={slot}",
                level="DEBUG",
            )
            return False

        # PATCH filament color
        result = self._spoolman_patch(f"/api/v1/filament/{filament_id}", {"color_hex": target_color})
        if result is not None:
            self.log(
                f"COLOR_SYNC filament_id={filament_id} old={existing_color} new={target_color} spool_id={spool_id} slot={slot}",
                level="INFO",
            )
            return True
        self.log(
            f"SYNC_COLOR_PATCH_FAILED filament_id={filament_id} spool_id={spool_id} slot={slot}",
            level="WARNING",
        )
        return False

    def _on_slot_assigned(self, event_name, data, kwargs):
        """Handle FILAMENT_IQ_SLOT_ASSIGNED: enroll lot_sig for non-RFID trays and reconcile the slot."""
        payload = data or {}
        slot = self._safe_int(payload.get("slot"), 0)
        spool_id = self._safe_int(payload.get("spool_id"), 0)
        sync_color_hex = str(payload.get("sync_color_hex", "") or "").strip()
        if slot not in self._tray_entity_by_slot or spool_id <= 0:
            return

        self._suppress_helper_change_until[slot] = datetime.datetime.utcnow() + datetime.timedelta(seconds=10)

        # Read tray attributes
        tray = self.get_state(self._tray_entity_by_slot[slot], attribute="all") or {}
        attrs = tray.get("attributes", {}) if isinstance(tray, dict) else {}
        tag_uid = str(attrs.get("tag_uid") or "").strip()
        tray_uuid = str(attrs.get("tray_uuid") or "").strip()

        # Skip RFID trays — already handled by bambu_rfid_manual_enroll_tag_to_spool
        if not self._is_all_zero_identity(tag_uid, tray_uuid):
            self.log(
                f"SLOT_ASSIGNED_RFID slot={slot} spool_id={spool_id} — skipping (RFID enroll handles this)",
                level="DEBUG",
            )
            self._run_reconcile(f"slot_assigned_slot_{slot}", slots_filter=[slot])
            return

        # Sync filament color before lot_sig build (color may change the sig)
        if sync_color_hex:
            spool_obj = self._spoolman_get(f"/api/v1/spool/{spool_id}")
            existing_lot_nr = ""
            if isinstance(spool_obj, dict):
                existing_lot_nr = str(spool_obj.get("lot_nr") or "").strip()
            is_rfid = len(existing_lot_nr) == 32 and all(
                c in "0123456789abcdefABCDEF" for c in existing_lot_nr
            )
            if not is_rfid:
                self._sync_filament_color_on_bind(slot, spool_id, sync_color_hex)
            else:
                self.log(f"SYNC_COLOR_SKIP_RFID slot={slot} spool_id={spool_id}", level="DEBUG")

        # Non-RFID: build lot_sig and enroll
        state_str = str(tray.get("state", "")) if isinstance(tray, dict) else ""
        tray_meta = self._tray_meta(attrs, state_str)
        lot_sig = self._build_lot_sig(tray_meta)

        if lot_sig:
            spools = self._spoolman_get("/api/v1/spool?limit=1000")
            if isinstance(spools, dict) and "items" in spools:
                spools = spools.get("items", [])
            spool_index = {self._safe_int(s.get("id"), 0): s for s in (spools if isinstance(spools, list) else [])}

            self._enroll_lot_nr(spool_id, lot_sig, spool_index, reason=f"manual_assign_slot_{slot}",
                                force=bool(sync_color_hex))
            self.log(
                f"SLOT_ASSIGNED_LOT_SIG_ENROLLED slot={slot} spool_id={spool_id} lot_sig={lot_sig}",
                level="INFO",
            )
        else:
            self.log(
                f"SLOT_ASSIGNED_LOT_SIG_EXISTS slot={slot} spool_id={spool_id} tray_meta={tray_meta}",
                level="WARNING",
            )

        # Single-slot reconcile
        self._run_reconcile(f"slot_assigned_slot_{slot}", slots_filter=[slot])

    def _on_validate_event(self, event_name, data, kwargs):
        """Field validation runner: reconcile single slot and log compact transcript."""
        payload = data or {}
        slot = self._safe_int(payload.get("slot"), 0)
        mode = str(payload.get("mode", "reinsert")).strip()
        if slot not in self._tray_entity_by_slot:
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
        try:
            self._run_reconcile_inner(reason, started, slots_filter, validation_mode, status_only)
        except Exception as exc:
            self.log(
                f"RECONCILE_ERROR unhandled exception: {exc}",
                level="ERROR",
            )
        finally:
            self._active_run = None
            self._reconcile_spools_cache = None

    def _run_reconcile_inner(self, reason, started, slots_filter=None, validation_mode=False, status_only=False):
        if slots_filter is not None:
            raw = slots_filter if isinstance(slots_filter, (list, tuple)) else [slots_filter]
            slots_to_process = [self._safe_int(s, 0) for s in raw if self._safe_int(s, 0) in self._tray_entity_by_slot]
        else:
            slots_to_process = list(self._tray_entity_by_slot.keys())

        # ── Print-active freeze: skip ALL writes during active prints ──
        try:
            print_active = str(
                self.get_state(self._print_active_entity) or ""
            ).lower() == "on"
        except Exception:
            print_active = False

        if print_active:
            if (
                self._print_active_since is not None
                and time.time() - self._print_active_since > 86400
            ):
                self.log(
                    "PRINT_FREEZE_WATCHDOG print_active on for >24h "
                    "— proceeding with reconcile",
                    level="WARNING",
                )
            else:
                self.log(
                    f"RECONCILE_SKIP_PRINT_ACTIVE reason={reason} "
                    f"slots={len(slots_to_process)}",
                    level="INFO",
                )
                return

        before_slots = {}
        for slot, entity_id in self._tray_entity_by_slot.items():
            if slot in slots_to_process:
                before_slots[str(slot)] = self._snapshot_slot(slot, entity_id)

        spools = self._spoolman_get("/api/v1/spool?limit=1000")
        if isinstance(spools, dict) and "items" in spools:
            spools = spools.get("items", [])
        if not isinstance(spools, list):
            self.log(
                "RECONCILE_SPOOLMAN_FETCH_FAILED: /api/v1/spool did not return a list",
                level="ERROR",
            )
            return
        # Cache for this reconcile pass — used by _clear_previous_occupant_guarded
        # to avoid re-fetching the full spool list per slot.
        self._reconcile_spools_cache = spools

        if self._pending_lot_nr_writes:
            pending = self._pending_lot_nr_writes
            self._pending_lot_nr_writes = {}
            spool_by_id = {self._safe_int(s.get("id"), 0): s for s in spools}
            for sid, lot_val in pending.items():
                target = spool_by_id.get(sid)
                if target is not None:
                    target["lot_nr"] = lot_val
                    self.log(f"PENDING_LOT_NR_MERGED spool_id={sid} lot_nr={lot_val}", level="INFO")

        # v4 lot_nr index: primary identity lookup (tray_uuid for RFID, sig for non-RFID)
        lotnr_to_spools = {}
        # Legacy RFID UID map (migration fallback): extra.rfid_tag_uid
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
            lot_nr = str(spool.get("lot_nr") or "").strip()
            if lot_nr:
                lotnr_to_spools.setdefault(lot_nr, []).append(spool_id)
            uid = self._extract_spool_uid(spool)
            if uid:
                tag_to_spools.setdefault(uid, []).append(spool_id)

        duplicate_uids = {uid for uid, spool_ids in tag_to_spools.items() if len(set(spool_ids)) > 1}
        spool_index = {self._safe_int(s.get("id"), 0): s for s in spools}

        ok = 0
        unbound = 0
        conflict = 0
        mismatch = 0

        for slot, entity_id in self._tray_entity_by_slot.items():
            if slot not in slots_to_process:
                continue
            writes_before_slot = len(self._active_run["writes"])
            tray = self.get_state(entity_id, attribute="all") or {}
            attrs = tray.get("attributes", {}) if isinstance(tray, dict) else {}
            raw_tag = attrs.get("tag_uid")
            tag_uid = self._canonicalize_tag_uid(raw_tag)
            # v4: tray_uuid is the primary RFID identity (spool factory serial)
            raw_tray_uuid = str(attrs.get("tray_uuid") or "").strip().replace(" ", "").replace("-", "").upper()
            tray_uuid = raw_tray_uuid if (raw_tray_uuid and raw_tray_uuid != "0" * len(raw_tray_uuid)) else ""
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
            stored_tray_sig = (self._get_helper_state(f"input_text.ams_slot_{slot}_tray_signature") or "") or ""
            helper_spool_id = self._safe_int(self._get_helper_state(f"input_text.ams_slot_{slot}_spool_id"), 0)
            previous_helper_spool_id = helper_spool_id
            helper_expected = self._safe_int(self._get_helper_state(f"input_text.ams_slot_{slot}_expected_spool_id"), 0)

            # TRUTH GUARD (RFID_VISIBLE): clear stale helper if its UID doesn't match physical tray tag
            norm_tag_tg = _normalize_rfid_tag_uid(tag_uid)
            rfid_visible = bool(norm_tag_tg and norm_tag_tg != "0000000000000000")

            _rfid_stuck_skip_matched = False
            # ── RFID identity-stuck tracker ──
            import time as _time_mod
            _rit = getattr(self, "_rfid_identity_tracker", None)
            if _rit is None:
                self._rfid_identity_tracker = {}
                _rit = self._rfid_identity_tracker
            _prev_entry = _rit.get(slot)
            if _prev_entry is None or _prev_entry["identity"] != current_tray_sig:
                _rit[slot] = {"identity": current_tray_sig, "change_ts": _time_mod.time()}
            # Check if RFID tag matches the assigned spool (lot_nr == tray_uuid)
            _stuck_spool_obj = spool_index.get(helper_spool_id) or {}
            _stuck_lot_nr = (_stuck_spool_obj.get("lot_nr") or "").strip()
            _stuck_tray_uuid = (tray_uuid or "").strip()
            _rfid_matches_spool = (
                helper_spool_id > 0
                and _stuck_lot_nr
                and _stuck_tray_uuid
                and _stuck_lot_nr.upper() == _stuck_tray_uuid.upper()
            )
            if _rfid_matches_spool:
                _rfid_stuck_skip_matched = True
            elif (reason.startswith("manual") and rfid_visible and not tray_empty
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

            # Skip truth guard when RFID_STUCK_SKIP already verified lot_nr == tray_uuid
            if rfid_visible and helper_spool_id > 0 and not _rfid_stuck_skip_matched:
                helper_spool_obj_tg = spool_index.get(helper_spool_id) or {}
                if not self._truth_guard_slot_patch(slot, t, tray_meta, tag_uid, helper_spool_id, helper_spool_obj_tg, tray_empty, tray_state_str, tray_uuid=tray_uuid):
                    helper_spool_id = 0
                    previous_helper_spool_id = 0

            # ── FORCE_ACCEPTED: user explicitly accepted this binding — do not overwrite ──
            current_unbound_reason = (self._get_helper_state(f"input_text.ams_slot_{slot}_unbound_reason") or "").strip()
            if current_unbound_reason == "FORCE_ACCEPTED" and helper_spool_id > 0:
                self.log(
                    f"FORCE_ACCEPTED_SKIP slot={slot} spool_id={helper_spool_id} — user force-accepted, preserving binding",
                    level="INFO",
                )
                status = STATUS_OK_NONRFID if not rfid_visible else STATUS_OK
                self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                # Don't touch unbound_reason — leave FORCE_ACCEPTED in place
                ok += 1
                t["decision"], t["reason"], t["action"] = "FORCE_ACCEPTED", "user_force_accepted", "preserve_binding"
                t["final_spool_id"] = helper_spool_id
                t["final_slot_status"] = status
                t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                self._active_run["validation_transcripts"].append(t)
                if validation_mode:
                    self._log_validation_transcript(t)
                continue

            # Bound invariant wins over pending: spool_id == expected_spool_id > 0 -> stay NON_RFID_REGISTERED
            # But NOT when tray is empty — empty tray must clear the binding.
            if not tag_uid and helper_spool_id > 0 and helper_expected > 0 and helper_spool_id == helper_expected and not tray_empty:
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
                t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                fid = helper_spool_id
                # v4: lot_nr convergence handled centrally at end of slot loop
                self._active_run["validation_transcripts"].append(t)
                if validation_mode:
                    self._log_validation_transcript(t)
                continue

            # Pending demotion: identity unavailable + actually pending + valid helper + stale/zero expected
            raw_tag_uid_pd = attrs.get("tag_uid") if attrs.get("tag_uid") is not None else ""
            raw_tray_uuid_pd = attrs.get("tray_uuid") if attrs.get("tray_uuid") is not None else ""
            identity_unavailable = not tag_uid and self._is_all_zero_identity(raw_tag_uid_pd, raw_tray_uuid_pd)
            stored_status = (self._get_helper_state(f"input_text.ams_slot_{slot}_status") or "").strip()
            pending_until_raw = (self._get_helper_state(f"input_text.ams_slot_{slot}_rfid_pending_until") or "").strip()
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
                t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                fid = helper_spool_id
                # v4: lot_nr convergence handled centrally at end of slot loop
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
                    self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str, tray_uuid=tray_uuid)
                    t["final_slot_status"] = status
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    continue

            # Non-RFID override: run before tag_uid-based branching so normalized "" or literal 000...0 all hit this path
            raw_tag_uid_ht = attrs.get("tag_uid") if attrs.get("tag_uid") is not None else ""
            raw_tray_uuid_ht = attrs.get("tray_uuid") if attrs.get("tray_uuid") is not None else ""
            nonrfid_entity = str(
                self.args.get(
                    "nonrfid_enabled_entity",
                    "input_boolean.filament_iq_nonrfid_enabled",
                )
            ).strip()
            nonrfid_enabled = (self.get_state(nonrfid_entity) or "").strip().lower() == "on"
            if nonrfid_enabled and self._is_all_zero_identity(raw_tag_uid_ht, raw_tray_uuid_ht):
                helper_entity = f"input_text.ams_slot_{slot}_spool_id"
                raw = self.get_state(helper_entity)
                try:
                    helper_spool_id = int(raw or 0)
                except (ValueError, TypeError, AttributeError):
                    helper_spool_id = 0
                self.log(
                    f"NONRFID_HELPER_READ slot={slot} entity_id={helper_entity} raw={raw!r} parsed={helper_spool_id}",
                    level="INFO",
                )
                if tray_empty and helper_spool_id > 0:
                    if not status_only:
                        self._clear_previous_occupant_guarded(slot, 0, spool_index)
                        self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                        self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                    self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_TRAY_EMPTY)
                    self._set_helper(f"input_text.ams_slot_{slot}_status", STATUS_UNBOUND_NO_TAG)
                    self.log(
                        f"NONRFID_EMPTY_TRAY_CLEAR slot={slot} reason=tray_empty prior_spool_id={helper_spool_id}",
                        level="INFO",
                    )
                    unbound += 1
                    t["decision"], t["reason"], t["action"] = "UNBOUND", "tray_empty", "nonrfid_empty_clear"
                    t["unbound_reason"] = UNBOUND_TRAY_EMPTY
                    t["final_slot_status"] = STATUS_UNBOUND_NO_TAG
                    t["final_spool_id"] = 0
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    continue
                if tray_empty:
                    continue
                self.log(
                    f"NONRFID_GUARD_HIT slot={slot} empty={tray_empty} raw_tag_uid={raw_tag_uid_ht!r} raw_tray_uuid={raw_tray_uuid_ht!r}",
                    level="INFO",
                )
                # ── Non-RFID signature + pending confirmation ──
                nonrfid_sig = self._build_tray_signature(tray_meta, tray_state_str, "")
                ht_confirmed, ht_pending = self._check_pending_confirmation(slot, nonrfid_sig, stored_tray_sig)

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

                fp_changed = not stored_tray_sig.startswith("PENDING:") and nonrfid_sig != stored_tray_sig and stored_tray_sig != ""
                just_confirmed = stored_tray_sig.startswith("PENDING:")
                ht_needs_rematch = fp_changed or just_confirmed or helper_spool_id <= 0

                if not ht_needs_rematch and helper_spool_id > 0 and not tray_empty:
                    # Detect physical spool swap: tray identity vs bound spool's lot_nr (or material+color if no lot_nr)
                    if not self._nonrfid_tray_matches_bound_spool(tray_meta, helper_spool_id, spool_index):
                        fid_raw_swap = (tray_meta.get("filament_id") or "").strip()
                        unbound_reason_swap = (self._get_helper_state(f"input_text.ams_slot_{slot}_unbound_reason") or "").strip()
                        # Generic filament with manual bind, or user force-accepted: do not treat as swap
                        preserve_bind = (
                            (is_generic_filament_id(fid_raw_swap) and helper_spool_id == helper_expected)
                            or unbound_reason_swap == "FORCE_ACCEPTED"
                        )
                        if preserve_bind:
                            self.log(
                                f"NONRFID_SPOOL_SWAP_SKIP_GENERIC_BOUND slot={slot} spool_id={helper_spool_id} — preserving manual bind",
                                level="INFO",
                            )
                        else:
                            spool_obj = spool_index.get(helper_spool_id) or {}
                            existing_lot_nr = str(spool_obj.get("lot_nr") or "").strip()
                            self.log(
                                f"NONRFID_SPOOL_SWAP_DETECTED slot={slot} helper={helper_spool_id} tray_sig={nonrfid_sig} spool_lot_nr={existing_lot_nr}",
                                level="INFO",
                            )
                            ht_needs_rematch = True
                            helper_spool_id = 0
                            if not status_only:
                                self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                                self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")

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
                            t["decision"], t["reason"], t["action"] = "NON_RFID", "helper_spool_not_found", "nonrfid_helper_cleared"
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
                            t["decision"], t["reason"], t["action"] = "NON_RFID", "spoolman_lookup_failed", "nonrfid_lookup_failed"
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
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "helper_spool_not_found", "nonrfid_helper_cleared"
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
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "truth_guard_material_mismatch", "nonrfid_material_mismatch"
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
                            slot, helper_spool_id, "", source="nonrfid_present",
                            tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=nonrfid_sig,
                            previous_helper_spool_id=previous_helper_spool_id,
                            spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                        )
                        # v4: enroll lot_nr sig instead of writing comment
                        lot_sig = self._build_lot_sig(tray_meta)
                        if lot_sig:
                            self._enroll_lot_nr(helper_spool_id, lot_sig, spool_index, reason="nonrfid_present")
                    status = STATUS_OK
                    ok += 1
                    t["decision"], t["reason"], t["action"] = "NON_RFID", "nonrfid_present", "nonrfid_registered"
                    t["final_spool_id"], t["selected_spool_id"] = helper_spool_id, helper_spool_id
                    t["final_slot_status"] = status
                    t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                    self.log(f"NONRFID_REGISTERED slot={slot} helper_spool_id={helper_spool_id}", level="DEBUG")
                    self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                    self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
                    self._log_slot_status_change(slot, status, "", helper_spool_id, tray_meta)
                    self._record_decision(slot, "nonrfid_present", {"helper_spool_id": helper_spool_id})
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    continue

                # ── v4 lot_nr lookup (runs before sentinel skip so enrolled generics still match) ──
                fid_raw = tray_meta.get("filament_id", "")
                lot_sig = self._build_lot_sig_for_lookup(tray_meta)
                if lot_sig:
                    lotnr_nonrfid_ids = list(set(lotnr_to_spools.get(lot_sig, [])))
                    # Unenrolled fallback runs for all trays (including generic); sentinel skip is last resort when zero candidates
                    unenrolled_ids = self._unenrolled_candidates_for_tray(tray_meta, spools, slot)
                    all_nonrfid_ids = list(set(lotnr_nonrfid_ids) | set(unenrolled_ids))
                    # Exclude RFID-enrolled spools (lot_nr = 32-char UUID); they must not match non-RFID trays
                    all_nonrfid_ids = [cid for cid in all_nonrfid_ids if not _is_lot_nr_uuid((spool_index.get(cid) or {}).get("lot_nr"))]
                    # Exclude spools actively bound to another slot (avoid slot-to-slot move stealing)
                    all_nonrfid_ids = [cid for cid in all_nonrfid_ids if not self._is_spool_active_in_other_slot(cid, slot)]
                    if len(all_nonrfid_ids) == 1:
                        resolved = all_nonrfid_ids[0]
                        if self._is_spool_active_in_other_slot(resolved, slot):
                            self.log(
                                f"NONRFID_LOT_NR_AMBIGUOUS slot={slot} sig={lot_sig} candidates={[resolved]} reason=spool_active_in_other_slot",
                                level="WARNING",
                            )
                            status = STATUS_NEEDS_MANUAL_BIND
                            if not status_only:
                                self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                                self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                            self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "AMBIGUOUS_SIG")
                            self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                            self._log_slot_status_change(slot, status, "", 0, tray_meta)
                            t["decision"], t["reason"], t["action"] = "NON_RFID", "ambiguous_sig", "needs_manual_bind"
                            t["unbound_reason"] = "AMBIGUOUS_SIG"
                            t["final_slot_status"] = status
                            t["final_spool_id"] = 0
                            self._active_run["validation_transcripts"].append(t)
                            if validation_mode:
                                self._log_validation_transcript(t)
                            unbound += 1
                            continue
                        from_unenrolled = resolved in set(unenrolled_ids)
                        if from_unenrolled:
                            self.log(f"NONRFID_UNENROLLED_MATCH slot={slot} spool_id={resolved} sig={lot_sig}", level="INFO")
                        else:
                            self.log(f"NONRFID_AUTO_MATCH slot={slot} spool_id={resolved} sig={lot_sig}", level="INFO")
                        if not status_only:
                            source = "nonrfid_unenrolled_match" if from_unenrolled else "nonrfid_lot_nr_match"
                            self._force_location_and_helpers(
                                slot, resolved, "", source=source,
                                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=nonrfid_sig,
                                previous_helper_spool_id=previous_helper_spool_id,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                            )
                            reason = "nonrfid_unenrolled_match" if from_unenrolled else "nonrfid_lot_nr_match"
                            self._enroll_lot_nr(resolved, lot_sig, spool_index, reason=reason)
                        status = STATUS_OK_NONRFID
                        ok += 1
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "lot_nr_match", "nonrfid_auto_match"
                        t["final_spool_id"], t["selected_spool_id"] = resolved, resolved
                        t["final_slot_status"] = status
                        t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
                        self._log_slot_status_change(slot, status, "", resolved, tray_meta)
                        self._record_decision(slot, "nonrfid_lot_nr_match", {"resolved_spool_id": resolved, "lot_sig": lot_sig})
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue
                    elif len(all_nonrfid_ids) > 1:
                        # Tiebreak: filter to spools available for this slot, pick lowest remaining_weight
                        current_slot_loc = self._normalize_location(self._canonical_location_by_slot.get(slot, ""))
                        available = []
                        for cid in all_nonrfid_ids:
                            cs = spool_index.get(cid)
                            if not isinstance(cs, dict):
                                continue
                            cloc = self._normalize_location(str(cs.get("location", "")).strip())
                            if cloc == current_slot_loc or cloc in ("shelf", "new", ""):
                                available.append(cs)
                        if len(available) == 1:
                            resolved = self._safe_int(available[0].get("id"), 0)
                            rem = self._safe_float(available[0].get("remaining_weight"), 0)
                            self.log(
                                f"NONRFID_AUTO_MATCH_TIEBREAK slot={slot} spool_id={resolved} sig={lot_sig} remaining={rem} "
                                f"reason=single_available filtered_from={len(all_nonrfid_ids)}",
                                level="INFO",
                            )
                        elif len(available) > 1:
                            available.sort(key=lambda s: self._safe_float(s.get("remaining_weight"), 0))
                            resolved = self._safe_int(available[0].get("id"), 0)
                            rem = self._safe_float(available[0].get("remaining_weight"), 0)
                            runner_up = self._safe_float(available[1].get("remaining_weight"), 0)
                            if rem < runner_up:
                                self.log(
                                    f"NONRFID_AUTO_MATCH_TIEBREAK slot={slot} spool_id={resolved} sig={lot_sig} remaining={rem} "
                                    f"runner_up={runner_up} reason=lowest_remaining",
                                    level="INFO",
                                )
                            else:
                                resolved = 0
                        else:
                            resolved = 0
                        if resolved > 0:
                            if self._is_spool_active_in_other_slot(resolved, slot):
                                self.log(
                                    f"NONRFID_LOT_NR_AMBIGUOUS slot={slot} sig={lot_sig} tiebreak_resolved={resolved} reason=spool_active_in_other_slot",
                                    level="WARNING",
                                )
                                resolved = 0
                            if resolved > 0:
                                from_unenrolled = resolved in set(unenrolled_ids)
                                if from_unenrolled:
                                    self.log(f"NONRFID_UNENROLLED_MATCH slot={slot} spool_id={resolved} sig={lot_sig}", level="INFO")
                                if not status_only:
                                    source = "nonrfid_unenrolled_match" if from_unenrolled else "nonrfid_lot_nr_tiebreak"
                                    self._force_location_and_helpers(
                                        slot, resolved, "", source=source,
                                        tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=nonrfid_sig,
                                        previous_helper_spool_id=previous_helper_spool_id,
                                        spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                                    )
                                    reason = "nonrfid_unenrolled_match" if from_unenrolled else "nonrfid_lot_nr_tiebreak"
                                    self._enroll_lot_nr(resolved, lot_sig, spool_index, reason=reason)
                                status = STATUS_OK_NONRFID
                                ok += 1
                                t["decision"], t["reason"], t["action"] = "NON_RFID", "lot_nr_tiebreak", "nonrfid_auto_match"
                                t["final_spool_id"], t["selected_spool_id"] = resolved, resolved
                                t["final_slot_status"] = status
                                t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                                self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                                self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
                                self._log_slot_status_change(slot, status, "", resolved, tray_meta)
                                self._record_decision(slot, "nonrfid_lot_nr_tiebreak", {"resolved_spool_id": resolved, "lot_sig": lot_sig})
                                self._active_run["validation_transcripts"].append(t)
                                if validation_mode:
                                    self._log_validation_transcript(t)
                                continue
                        self.log(
                            f"NONRFID_LOT_NR_AMBIGUOUS slot={slot} sig={lot_sig} candidates={all_nonrfid_ids}",
                            level="WARNING",
                        )
                        status = STATUS_NEEDS_MANUAL_BIND
                        if not status_only:
                            self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                            self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "AMBIGUOUS_SIG")
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, "", 0, tray_meta)
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "ambiguous_sig", "needs_manual_bind"
                        t["unbound_reason"] = "AMBIGUOUS_SIG"
                        t["final_spool_id"] = 0
                        t["final_slot_status"] = status
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        unbound += 1
                        continue

                # ── Generic sentinel last resort: only when zero lot_nr + zero unenrolled candidates ──
                if is_generic_filament_id(fid_raw):
                    self.log(
                        f"NONRFID_SENTINEL_SKIP slot={slot} filament_id={fid_raw} reason=GENERIC_FILAMENT_NO_AUTO_MATCH",
                        level="INFO",
                    )
                    # Preserve manual bind when bound invariant or user force-accepted
                    sentinel_unbound = (self._get_helper_state(f"input_text.ams_slot_{slot}_unbound_reason") or "").strip()
                    if helper_spool_id > 0 and (helper_spool_id == helper_expected or sentinel_unbound == "FORCE_ACCEPTED"):
                        self.log(
                            f"NONRFID_SENTINEL_MANUAL_BIND_PRESERVED slot={slot} spool_id={helper_spool_id} "
                            f"— generic filament but manual bind exists, keeping binding",
                            level="INFO",
                        )
                        status = STATUS_OK_NONRFID
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
                        self._set_helper(f"input_text.ams_slot_{slot}_tray_signature", nonrfid_sig)
                        self._log_slot_status_change(slot, status, "", helper_spool_id, tray_meta)
                        ok += 1
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "generic_sentinel_manual_preserved", "nonrfid_registered"
                        t["final_spool_id"] = helper_spool_id
                        t["final_slot_status"] = status
                        t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue
                    # No manual bind — flag for user
                    status = STATUS_NEEDS_MANUAL_BIND
                    if not status_only:
                        self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                        self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                    self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_NONRFID_NO_MATCH)
                    self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                    self._log_slot_status_change(slot, status, "", 0, tray_meta)
                    t["decision"], t["reason"], t["action"] = "NON_RFID", "generic_sentinel", "needs_manual_bind"
                    t["unbound_reason"] = UNBOUND_NONRFID_NO_MATCH
                    t["final_spool_id"] = 0
                    t["final_slot_status"] = status
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    unbound += 1
                    continue

                # ── Fingerprint changed / just confirmed / unbound → auto-match if confident ──
                confident = self._is_confident_nonrfid(attrs, tray_state_str, fid_raw)
                if confident:
                    # v4 Step 3 — migration fallback: HA_SIG in comment → promote to lot_nr
                    if lot_sig:
                        ha_sig_test = self._compute_ha_sig(tray_meta, slot=slot, spool_index=spool_index, expected_spool_id=0, candidate_ids=[])
                        if ha_sig_test:
                            comment_matches = self._find_flow_b_candidates(spools, ha_sig_test)
                            if len(comment_matches) == 1:
                                resolved = self._safe_int(comment_matches[0].get("id"), 0)
                                if resolved > 0:
                                    self.log(f"NONRFID_COMMENT_MIGRATION slot={slot} ha_sig={ha_sig_test} spool_id={resolved}", level="INFO")
                                    if not status_only:
                                        self._force_location_and_helpers(
                                            slot, resolved, "", source="nonrfid_comment_migration",
                                            tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=nonrfid_sig,
                                            previous_helper_spool_id=previous_helper_spool_id,
                                            spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                                        )
                                        self._enroll_lot_nr(resolved, lot_sig, spool_index, reason="comment_migration")
                                    status = STATUS_OK_NONRFID
                                    ok += 1
                                    t["decision"], t["reason"], t["action"] = "NON_RFID", "comment_migration", "nonrfid_comment_migration"
                                    t["final_spool_id"], t["selected_spool_id"] = resolved, resolved
                                    t["final_slot_status"] = status
                                    t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                                    self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                                    self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
                                    self._log_slot_status_change(slot, status, "", resolved, tray_meta)
                                    self._record_decision(slot, "nonrfid_comment_migration", {"resolved_spool_id": resolved, "ha_sig": ha_sig_test})
                                    self._active_run["validation_transcripts"].append(t)
                                    if validation_mode:
                                        self._log_validation_transcript(t)
                                    continue

                    # Step 4 — filament_id exact match (before vendor+material)
                    fid_resolved = self._try_filament_id_match(spools, fid_raw, slot, spool_index, nonrfid_sig,
                                                                tray_meta, tray, previous_helper_spool_id,
                                                                t, tray_empty, tray_state_str, status_only, validation_mode)
                    if fid_resolved is not None:
                        status = STATUS_OK_NONRFID
                        ok += 1
                        t["final_slot_status"] = status
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue

                    # Step 4 — Vendor + material match
                    shelf_ids, _ = self._find_deterministic_candidates(spools, tray_meta, slot)
                    if len(shelf_ids) == 1:
                        resolved = shelf_ids[0]
                        if not status_only:
                            self._force_location_and_helpers(
                                slot, resolved, "", source="nonrfid_auto_match",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=nonrfid_sig,
                                previous_helper_spool_id=previous_helper_spool_id,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                            )
                        status = STATUS_OK_NONRFID
                        ok += 1
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "nonrfid_auto_match", "nonrfid_auto_match"
                        t["final_spool_id"], t["selected_spool_id"] = resolved, resolved
                        t["final_slot_status"] = status
                        t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
                        self._log_slot_status_change(slot, status, "", resolved, tray_meta)
                        self._record_decision(slot, "nonrfid_auto_match", {"resolved_spool_id": resolved})
                        # v4: lot_nr enrollment for auto match
                        lot_sig_conv = self._build_lot_sig(tray_meta)
                        if lot_sig_conv and not status_only:
                            self._enroll_lot_nr(resolved, lot_sig_conv, spool_index, reason="nonrfid_auto_match")
                    else:
                        status = STATUS_NEEDS_MANUAL_BIND
                        if not status_only:
                            self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                            self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_NONRFID_NO_MATCH)
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, "", 0, tray_meta)
                        t["decision"], t["reason"], t["action"] = "NON_RFID", "nonrfid_no_match", "needs_manual_bind"
                        t["unbound_reason"] = UNBOUND_NONRFID_NO_MATCH
                        t["final_spool_id"] = 0
                        notified = getattr(self, "_nonrfid_nomatch_notified", None)
                        if notified is None:
                            self._nonrfid_nomatch_notified = set()
                            notified = self._nonrfid_nomatch_notified
                        fp_key = f"{slot}:{nonrfid_sig}"
                        if fp_key not in notified:
                            notified.add(fp_key)
                            self.log(f"NONRFID_NO_MATCH slot={slot} fingerprint={nonrfid_sig} -> NEEDS_MANUAL_BIND", level="WARNING")
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
            if slot not in self._canonical_location_by_slot:
                self.log(
                    f"slot={slot} is in self._tray_entity_by_slot but missing from self._canonical_location_by_slot; cannot persist (missing_canonical_location)",
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
                        t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                        fid = helper_spool_id
                        # v4: lot_nr convergence handled centrally at end of slot loop; but this path continues
                        lot_sig_conv = self._build_lot_sig(tray_meta)
                        if lot_sig_conv and not status_only:
                            self._enroll_lot_nr(helper_spool_id, lot_sig_conv, spool_index, reason="nonrfid_converge")
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue
                status = STATUS_UNBOUND_NO_TAG
                t["decision"], t["reason"], t["action"] = "UNBOUND", "no_tag", "unbound_no_tag"
                self._record_no_write(slot, "no_tag_uid")
                nonrfid_entity = str(
                    self.args.get(
                        "nonrfid_enabled_entity",
                        "input_boolean.filament_iq_nonrfid_enabled",
                    )
                ).strip()
                nonrfid_enabled = (self.get_state(nonrfid_entity) or "").strip().lower() == "on"
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
                        t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, "", resolved_spool_id, tray_meta)
                        self._record_decision(slot, "nonrfid_shelf_match", {"resolved_spool_id": resolved_spool_id})
                        fid = int(resolved_spool_id or 0)
                        # v4: lot_nr enrollment for shelf match
                        lot_sig_conv = self._build_lot_sig(tray_meta)
                        if lot_sig_conv and not status_only:
                            self._enroll_lot_nr(resolved_spool_id, lot_sig_conv, spool_index, reason="nonrfid_shelf_match")
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
                            t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                            self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                            self._log_slot_status_change(slot, status, "", resolved_spool_id, tray_meta)
                            self._record_decision(slot, "nonrfid_shelf_tiebreak", {"resolved_spool_id": resolved_spool_id})
                            fid = int(resolved_spool_id or 0)
                            # v4: lot_nr enrollment for shelf tiebreak
                            lot_sig_conv = self._build_lot_sig(tray_meta)
                            if lot_sig_conv and not status_only:
                                self._enroll_lot_nr(resolved_spool_id, lot_sig_conv, spool_index, reason="nonrfid_shelf_tiebreak")
                            self._active_run["validation_transcripts"].append(t)
                            if validation_mode:
                                self._log_validation_transcript(t)
                            continue
                        status = STATUS_UNBOUND_ACTION_REQUIRED
                        unbound += 1
                        t["decision"], t["reason"], t["action"] = "UNBOUND", "nonrfid_ambiguous_shelf", "unbound_needs_action"
                        t["final_slot_status"] = status
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str, tray_uuid=tray_uuid)
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
                        t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, "", resolved_spool_id, tray_meta)
                        self._record_decision(slot, "nonrfid_new_fallback", {"resolved_spool_id": resolved_spool_id})
                        fid = int(resolved_spool_id or 0)
                        # v4: lot_nr enrollment for new fallback
                        lot_sig_conv = self._build_lot_sig(tray_meta)
                        if lot_sig_conv and not status_only:
                            self._enroll_lot_nr(resolved_spool_id, lot_sig_conv, spool_index, reason="nonrfid_new_fallback")
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
                    self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str, tray_uuid=tray_uuid)
                    self._notify_nonrfid_needs_action(slot, tray_meta, reason_detail)
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    continue
            else:
                # All slots: write tray identity when tray has data (sticky key)
                if tray_state_str not in ("unknown", "unavailable", "", "empty") and current_tray_sig:
                    self._set_helper(f"input_text.ams_slot_{slot}_tray_signature", current_tray_sig)
                # v4: primary lookup by tray_uuid in lot_nr, fallback to tag_uid in extra
                slot_tag = _normalize_rfid_tag_uid(tag_uid)
                _lotnr_match = False
                if tray_uuid:
                    mapped_ids = list(set(lotnr_to_spools.get(tray_uuid, [])))
                    if mapped_ids:
                        _lotnr_match = True
                else:
                    mapped_ids = []
                if not mapped_ids:
                    mapped_ids = list(set(tag_to_spools.get(slot_tag, [])))
                if len(mapped_ids) == 1:
                    resolved_spool_id = mapped_ids[0]
                    uid_matched = resolved_spool_id > 0
                    expected_spool_id = self._safe_int(self._get_helper_state(f"input_text.ams_slot_{slot}_expected_spool_id"), 0)
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
                                if not self._rfid_bind_guard_ok(resolved_spool_id, tag_uid, spool_index, tray_uuid=tray_uuid):
                                    self._apply_rfid_bind_guard_fail(slot, t, tray_meta, tag_uid, resolved_spool_id, validation_mode)
                                    unbound += 1
                                    continue
                                self._force_location_and_helpers(
                                    slot, resolved_spool_id, tag_uid, source="expected_autofix",
                                    tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=current_tray_sig,
                                    previous_helper_spool_id=previous_helper_spool_id,
                                    spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                                    tray_uuid=tray_uuid,
                                )
                                # v4: enroll tray_uuid to lot_nr if not already set
                                if tray_uuid:
                                    self._enroll_lot_nr(resolved_spool_id, tray_uuid, spool_index, reason="expected_autofix")
                            t["converge_reason"] = "expected_autofix"
                            status = STATUS_OK_FIXED_EXPECTED
                            ok += 1
                            t["decision"], t["reason"], t["action"] = "OK", "FIXED_EXPECTED", "expected_autofix"
                            t["uid_lookup_count"], t["final_spool_id"], t["selected_spool_id"] = 1, resolved_spool_id, resolved_spool_id
                            t["final_location"] = self._canonical_location_by_slot[slot]
                            self._record_decision(
                                slot,
                                "expected_autofix",
                                {"tag_uid": tag_uid, "resolved_spool_id": resolved_spool_id, "previous_expected": expected_spool_id},
                            )
                            expected_hex = self._normalize_tray_hex(self._get_helper_state(f"input_text.ams_slot_{slot}_expected_color_hex"))
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
                            if not self._rfid_bind_guard_ok(resolved_spool_id, tag_uid, spool_index, tray_uuid=tray_uuid):
                                self._apply_rfid_bind_guard_fail(slot, t, tray_meta, tag_uid, resolved_spool_id, validation_mode)
                                unbound += 1
                                continue
                            self._force_location_and_helpers(
                                slot, resolved_spool_id, tag_uid, source="known_binding",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=current_tray_sig,
                                previous_helper_spool_id=previous_helper_spool_id,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                                tray_uuid=tray_uuid,
                            )
                            # v4: enroll tray_uuid to lot_nr if not already set
                            if tray_uuid:
                                self._enroll_lot_nr(resolved_spool_id, tray_uuid, spool_index, reason="known_binding")
                        t["converge_reason"] = "known_binding"
                        status = STATUS_OK
                        ok += 1
                        t["decision"], t["reason"], t["action"] = "OK", "known_binding", "known_uid_bind"
                        t["uid_lookup_count"], t["final_spool_id"], t["selected_spool_id"] = 1, resolved_spool_id, resolved_spool_id
                        t["final_location"] = self._canonical_location_by_slot[slot]
                        self._record_decision(
                            slot,
                            "known_binding",
                            {"tag_uid": tag_uid, "resolved_spool_id": resolved_spool_id, "result": "ok"},
                        )
                        expected_hex = self._normalize_tray_hex(self._get_helper_state(f"input_text.ams_slot_{slot}_expected_color_hex"))
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
                        t["final_spool_id"], t["selected_spool_id"], t["final_location"] = resolved_spool_id, resolved_spool_id, self._canonical_location_by_slot[slot]
                        if not status_only:
                            if self._may_stick_override(slot, resolved_spool_id, helper_spool_id, tag_uid, spool_index, current_tray_sig, stored_tray_sig):
                                resolved_spool_id = helper_spool_id
                            if not self._rfid_bind_guard_ok(resolved_spool_id, tag_uid, spool_index, tray_uuid=tray_uuid):
                                self._apply_rfid_bind_guard_fail(slot, t, tray_meta, tag_uid, resolved_spool_id, validation_mode)
                                unbound += 1
                                continue
                            self._force_location_and_helpers(
                                slot, resolved_spool_id, tag_uid, source="rfid_shelf_tiebreak",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=current_tray_sig,
                                previous_helper_spool_id=previous_helper_spool_id,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                                tray_uuid=tray_uuid,
                            )
                            # v4: enroll tray_uuid to lot_nr if not already set
                            if tray_uuid:
                                self._enroll_lot_nr(resolved_spool_id, tray_uuid, spool_index, reason="rfid_shelf_tiebreak")
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
                    # Tier 2 — AMS slots with empty tray (RFID only)
                    tier2_candidates = self._find_tier2_candidates(slot, tag_uid, tray_meta, spool_index, tray_uuid=tray_uuid)
                    if tier2_candidates:
                        if len(tier2_candidates) == 1:
                            resolved_spool_id = self._safe_int(tier2_candidates[0].get("id"), 0)
                        else:
                            winner_id, _ = tiebreak_choose_spool(tier2_candidates, strict_mode=False)
                            resolved_spool_id = winner_id or 0
                        if resolved_spool_id > 0:
                            old_loc = str(tier2_candidates[0].get("location", ""))
                            new_loc = self._canonical_location_by_slot.get(slot, "")
                            self.log(
                                f"TIER2_MATCH slot={slot} spool_id={resolved_spool_id} tag_uid={tag_uid}",
                                level="INFO",
                            )
                            self.log(
                                f"TIER2_LOCATION_UPDATE slot={slot} spool_id={resolved_spool_id} from={old_loc} to={new_loc}",
                                level="INFO",
                            )
                            if not status_only:
                                if not self._rfid_bind_guard_ok(resolved_spool_id, tag_uid, spool_index, tray_uuid=tray_uuid):
                                    self._apply_rfid_bind_guard_fail(slot, t, tray_meta, tag_uid, resolved_spool_id, validation_mode)
                                    unbound += 1
                                    continue
                                self._force_location_and_helpers(
                                    slot, resolved_spool_id, tag_uid, source="tier2_ams_empty",
                                    tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=current_tray_sig,
                                    previous_helper_spool_id=previous_helper_spool_id,
                                    spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                                    tray_uuid=tray_uuid,
                                )
                                # v4: enroll tray_uuid to lot_nr if not already set
                                if tray_uuid:
                                    self._enroll_lot_nr(resolved_spool_id, tray_uuid, spool_index, reason="tier2_ams_empty")
                            t["converge_reason"] = "tier2_ams_empty"
                            status = STATUS_OK
                            ok += 1
                            t["decision"], t["reason"], t["action"] = "OK", "TIER2_AMS_EMPTY", "tier2_bind"
                            t["uid_lookup_count"] = 1
                            t["final_spool_id"], t["selected_spool_id"] = resolved_spool_id, resolved_spool_id
                            t["final_location"] = new_loc
                            self._record_decision(slot, "tier2_ams_empty", {"tag_uid": tag_uid, "resolved_spool_id": resolved_spool_id})
                            self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                            self._log_slot_status_change(slot, status, tag_uid, resolved_spool_id, tray_meta)
                            t["final_slot_status"] = status
                            self._active_run["validation_transcripts"].append(t)
                            if validation_mode:
                                self._log_validation_transcript(t)
                            continue

                    # RFID sig-based fallback: try type|filament_id|color_hex match (enrolled + unenrolled spools) before NEEDS_ACTION
                    lot_sig = self._build_lot_sig_for_lookup(tray_meta) if tray_meta else ""
                    sig_fallback_resolved = 0
                    if lot_sig and tray_uuid:
                        # 1) Candidates from lot_nr index (already enrolled with this sig)
                        sig_candidate_ids = list(set(lotnr_to_spools.get(lot_sig, [])))
                        # 2) Unenrolled spools: same search as non-RFID path
                        unenrolled_ids = self._unenrolled_candidates_for_tray(tray_meta, spools, slot)
                        all_sig_ids = list(set(sig_candidate_ids) | set(unenrolled_ids))
                        candidate_spool_dicts = []
                        for cid in all_sig_ids:
                            spool_obj = spool_index.get(cid)
                            if isinstance(spool_obj, dict):
                                candidate_spool_dicts.append(spool_obj)
                        if len(candidate_spool_dicts) == 1:
                            sig_fallback_resolved = self._safe_int(candidate_spool_dicts[0].get("id"), 0)
                        elif len(candidate_spool_dicts) > 1:
                            winner_id, _ = tiebreak_choose_spool(candidate_spool_dicts, strict_mode=False)
                            sig_fallback_resolved = winner_id or 0

                    if sig_fallback_resolved > 0:
                        resolved_spool_id = sig_fallback_resolved
                        self.log(
                            f"RFID_AUTO_ENROLLED slot={slot} spool_id={resolved_spool_id} tray_uuid={tray_uuid} sig={lot_sig}",
                            level="INFO",
                        )
                        if not status_only:
                            self._enroll_lot_nr(resolved_spool_id, tray_uuid, spool_index, reason="rfid_auto_enrolled")
                            lotnr_to_spools.setdefault(tray_uuid, [])
                            if resolved_spool_id not in lotnr_to_spools[tray_uuid]:
                                lotnr_to_spools[tray_uuid].append(resolved_spool_id)
                            if not self._rfid_bind_guard_ok(resolved_spool_id, tag_uid, spool_index, tray_uuid=tray_uuid):
                                self._apply_rfid_bind_guard_fail(slot, t, tray_meta, tag_uid, resolved_spool_id, validation_mode)
                                unbound += 1
                                continue
                            self._force_location_and_helpers(
                                slot, resolved_spool_id, tag_uid, source="rfid_auto_enrolled",
                                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=current_tray_sig,
                                previous_helper_spool_id=previous_helper_spool_id,
                                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
                                tray_uuid=tray_uuid,
                            )
                        status = STATUS_OK
                        ok += 1
                        t["decision"], t["reason"], t["action"] = "OK", "rfid_auto_enrolled", "rfid_auto_enrolled"
                        t["uid_lookup_count"], t["final_spool_id"], t["selected_spool_id"] = 1, resolved_spool_id, resolved_spool_id
                        t["final_location"] = self._canonical_location_by_slot.get(slot, "")
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, tag_uid, resolved_spool_id, tray_meta)
                        t["final_slot_status"] = status
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue

                    # If sig fallback ran but was ambiguous or zero candidates -> NEEDS_MANUAL_BIND
                    if lot_sig and tray_uuid:
                        all_sig_ids = list(set(lotnr_to_spools.get(lot_sig, [])) | set(unenrolled_ids))
                        all_sig_ids = [c for c in all_sig_ids if not self._is_spool_active_in_other_slot(c, slot)]
                        candidate_spool_dicts = [spool_index[c] for c in all_sig_ids if spool_index.get(c) and isinstance(spool_index.get(c), dict)]
                        if len(candidate_spool_dicts) > 1:
                            status = STATUS_NEEDS_MANUAL_BIND
                            unbound += 1
                            t["decision"], t["reason"], t["action"] = "UNBOUND", "AMBIGUOUS_SIG", "needs_manual_bind"
                            t["unbound_reason"] = "AMBIGUOUS_SIG"
                            t["final_spool_id"] = 0
                            t["final_slot_status"] = status
                            if not status_only:
                                self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                                self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                            self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "AMBIGUOUS_SIG")
                            self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                            self._log_slot_status_change(slot, status, tag_uid, 0, tray_meta)
                            self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str, tray_uuid=tray_uuid)
                            self._active_run["validation_transcripts"].append(t)
                            if validation_mode:
                                self._log_validation_transcript(t)
                            continue
                        # zero candidates
                        status = STATUS_NEEDS_MANUAL_BIND
                        unbound += 1
                        t["decision"], t["reason"], t["action"] = "UNBOUND", "NO_CANDIDATE", "needs_manual_bind"
                        t["unbound_reason"] = "NO_CANDIDATE"
                        t["final_spool_id"] = 0
                        t["final_slot_status"] = status
                        if not status_only:
                            self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                            self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "NO_CANDIDATE")
                        self._set_helper(f"input_text.ams_slot_{slot}_status", status)
                        self._log_slot_status_change(slot, status, tag_uid, 0, tray_meta)
                        self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str, tray_uuid=tray_uuid)
                        self._active_run["validation_transcripts"].append(t)
                        if validation_mode:
                            self._log_validation_transcript(t)
                        continue

                    # RFID no eligible match after Tier 1+2 and no sig fallback => NEEDS_ACTION
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
                    self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str, tray_uuid=tray_uuid)
                    self._active_run["validation_transcripts"].append(t)
                    if validation_mode:
                        self._log_validation_transcript(t)
                    continue


            if status.startswith("UNBOUND"):
                unbound += 1 if status != STATUS_UNBOUND_MANUAL_CREATE else 0
                if status == STATUS_UNBOUND_NO_TAG and not status_only:
                    self._force_location_and_helpers(
                        slot, 0, "", source="unbind", previous_helper_spool_id=previous_helper_spool_id,
                        spool_index=spool_index,
                    )

            self._set_helper(f"input_text.ams_slot_{slot}_status", status)
            self._log_slot_status_change(slot, status, tag_uid, resolved_spool_id, tray_meta)

            t["final_slot_status"] = status
            if status.startswith("UNBOUND"):
                self._apply_unbound_reason(slot, t, tray_meta, tag_uid, tray_empty, tray_state_str, tray_uuid=tray_uuid)
            # v4: lot_nr convergence — enroll identity on every resolved bind
            fid = int(t.get("final_spool_id") or 0)
            if _should_converge_ha_sig(status_only, status, fid):
                self._converge_lot_nr(slot, fid, tray_meta, spool_index, tray_uuid=tray_uuid, tag_uid=tag_uid or "")
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
        for slot, entity_id in self._tray_entity_by_slot.items():
            if slot in slots_to_process:
                after_slots[str(slot)] = self._snapshot_slot(slot, entity_id)
        mapping = {}
        for slot in self._tray_entity_by_slot:
            sid = self._safe_int(self._get_helper_state(f"input_text.ams_slot_{slot}_expected_spool_id"), 0)
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
        if status_only and reason == "safety_poll" and (unbound > 0 or conflict > 0 or mismatch > 0):
            self.log(
                f"SAFETY_POLL_DRIFT detected: one or more slots not OK. Trigger manual reconcile ({self._reconcile_button_entity}) to correct.",
                level="WARNING",
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
        suppress_until = datetime.datetime.utcnow() + datetime.timedelta(seconds=5)
        for s in slots_to_process:
            self._suppress_helper_change_until[s] = suppress_until

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

    def _is_spool_active_in_other_slot(self, spool_id, current_slot):
        """True if spool_id is the current spool_id helper of any slot (other than current_slot) whose tray is not empty."""
        if spool_id <= 0:
            return False
        for other_slot in self._physical_ams_slots:
            if other_slot == current_slot:
                continue
            ot_entity = self._tray_entity_by_slot.get(other_slot)
            if not ot_entity:
                continue
            ot_tray = self.get_state(ot_entity, attribute="all") or {}
            ot_state = str(ot_tray.get("state", "") if isinstance(ot_tray, dict) else ot_tray or "").strip().lower()
            if ot_state == "empty":
                continue
            other_helper = self._safe_int(self._get_helper_state(f"input_text.ams_slot_{other_slot}_spool_id"), 0)
            if other_helper == spool_id:
                self.log(
                    f"PREV_OCCUPANT_ACTIVE_IN_OTHER_SLOT spool_id={spool_id} current_slot={current_slot} active_slot={other_slot}",
                    level="INFO",
                )
                return True
        return False

    def _clear_previous_occupant_guarded(self, slot, new_spool_id, spool_index):
        """Move previous slot occupant to Shelf or Empty, with guards.
        Only moves if:
        1. Previous occupant's *live* Spoolman location matches this slot's canonical location
        2. Previous occupant is not active in any other slot

        Uses _reconcile_spools_cache (set at start of _run_reconcile) to avoid
        re-fetching the full spool list per slot. Falls back to spool_index values.
        """
        slot_loc = self._canonical_location_by_slot.get(slot)
        if not slot_loc:
            return
        norm_slot_loc = self._normalize_location(slot_loc)

        live_spools = getattr(self, "_reconcile_spools_cache", None)
        if live_spools is None:
            live_spools = list((spool_index or {}).values())

        prev_spools = []
        for spool in live_spools:
            sid = self._safe_int(spool.get("id"), 0)
            if sid <= 0 or sid == new_spool_id:
                continue
            spool_loc = self._normalize_location(str(spool.get("location", "")).strip())
            if spool_loc == norm_slot_loc:
                prev_spools.append(spool)

        if not prev_spools:
            self.log(f"PREV_OCCUPANT_NONE slot={slot}", level="DEBUG")
            return

        for prev in prev_spools:
            prev_id = self._safe_int(prev.get("id"), 0)
            if prev_id <= 0:
                continue
            if self._is_spool_active_in_other_slot(prev_id, slot):
                self.log(
                    f"PREV_OCCUPANT_SKIP slot={slot} spool_id={prev_id} reason=active_in_other_slot",
                    level="INFO",
                )
                continue
            rem = self._safe_float(prev.get("remaining_weight"), -1.0)
            if rem <= 0 and rem != -1.0:
                dest = LOCATION_EMPTY
            else:
                dest = LOCATION_NOT_IN_AMS
            self._spoolman_patch(f"/api/v1/spool/{prev_id}", {"location": dest})
            self.log(
                f"PREV_OCCUPANT_MOVED slot={slot} spool_id={prev_id} from={norm_slot_loc} to={dest} remaining={rem}",
                level="INFO",
            )

    def _force_location_and_helpers(self, slot, spool_id, tag_uid, source, tray_meta=None, tray_state="", tray_identity=None, previous_helper_spool_id=0, spool_index=None, t=None, tray_empty=False, tray_state_str="", tray_uuid=""):
        slot_loc = self._canonical_location_by_slot.get(slot)
        if not slot_loc:
            return
        slot_loc = self._normalize_location(slot_loc)

        if spool_id > 0 and spool_index is not None:
            helper_spool_obj = spool_index.get(spool_id)
            if not self._truth_guard_slot_patch(slot, t or {}, tray_meta or {}, tag_uid, spool_id, helper_spool_obj, tray_empty, tray_state_str, tray_uuid=tray_uuid):
                self.log(f"TRUTH_GUARD_FORCE_LOC_BLOCK slot={slot} spool_id={spool_id} source={source}", level="INFO")
                return

        self._clear_previous_occupant_guarded(slot, spool_id, spool_index)

        # Unbind: set helper to 0 and tray_signature to "" then return (no new location write).
        if spool_id == 0:
            # Guard: do not clear bindings during active print
            try:
                if str(self.get_state(self._print_active_entity) or "").lower() == "on":
                    self.log(
                        f"BINDING_HELD_DURING_PRINT slot={slot} reason=print_active",
                        level="INFO",
                    )
                    return
            except Exception:
                pass  # if entity unavailable, proceed with unbind
            self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
            self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
            self._set_helper(f"input_text.ams_slot_{slot}_tray_signature", "")
            return

        desired_location = slot_loc
        current_location = str((spool_index or {}).get(spool_id, {}).get("location", "")) if spool_index else ""
        if not current_location:
            try:
                spool_data = self._spoolman_get(f"/api/v1/spool/{spool_id}")
                current_location = str(spool_data.get("location", "")) if isinstance(spool_data, dict) else ""
            except Exception:
                current_location = ""
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
        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
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

    def _enroll_lot_nr(self, spool_id, lot_nr_value, spool_index, reason="enroll", force=False):
        """Write lot_nr to Spoolman on first bind. Refuses to overwrite existing different lot_nr
        unless the existing value has empty pipe-delimited fields and the new value refines them,
        or force=True (used after color sync to re-enroll with corrected sig)."""
        if not lot_nr_value:
            return
        spool = spool_index.get(spool_id) or self._spoolman_get(f"/api/v1/spool/{spool_id}")
        existing = str(spool.get("lot_nr") or "").strip() if isinstance(spool, dict) else ""
        if existing == lot_nr_value:
            self._record_no_write(spool_id, "lot_nr_already_set", {"spool_id": spool_id, "lot_nr": lot_nr_value})
            return
        if existing and existing != lot_nr_value:
            if force:
                self.log(
                    f"LOT_NR_FORCE_OVERWRITE spool_id={spool_id} old={existing} new={lot_nr_value} reason=color_sync_re_enrollment",
                    level="INFO",
                )
            # Allow overwrite if existing has empty pipe fields and new value refines them
            elif self._lot_nr_is_refinement(existing, lot_nr_value):
                self.log(
                    f"LOT_NR_REFINE spool_id={spool_id} existing={existing} incoming={lot_nr_value} reason=refine_empty_fields",
                    level="INFO",
                )
            else:
                self.log(
                    f"LOT_NR_CONFLICT spool_id={spool_id} existing={existing} incoming={lot_nr_value} reason=refuse_overwrite",
                    level="WARNING",
                )
                return
        self._patch_spool_fields(spool_id, {"lot_nr": lot_nr_value})
        if isinstance(spool_index, dict) and spool_id in spool_index:
            spool_index[spool_id]["lot_nr"] = lot_nr_value
        self.log(f"LOT_NR_ENROLLED spool_id={spool_id} lot_nr={lot_nr_value} reason={reason}")

    def _lot_nr_is_refinement(self, existing, incoming):
        """True if incoming lot_nr is a refinement of existing — same type,
        and any empty fields in existing are populated in incoming.

        Both must be pipe-delimited non-RFID sigs (type|filament_id|color_hex).
        RFID lot_nr values (32-char hex UUIDs) are never refinable.
        """
        # Don't refine RFID UUIDs
        if _is_lot_nr_uuid(existing) or _is_lot_nr_uuid(incoming):
            return False
        ex_parts = existing.split("|")
        in_parts = incoming.split("|")
        # Must be same format (3 pipe-delimited fields)
        if len(ex_parts) != 3 or len(in_parts) != 3:
            return False
        # Type (field 0) must match
        if ex_parts[0] != in_parts[0]:
            return False
        # Existing must have at least one empty or all-zero field
        has_empty = False
        for i in range(3):
            ex_val = ex_parts[i].strip()
            in_val = in_parts[i].strip()
            if not ex_val or ex_val == "000000":
                if in_val and in_val != "000000":
                    has_empty = True
            elif ex_val != in_val:
                # Non-empty existing field differs from incoming — not a refinement
                return False
        return has_empty

    def _bind_uid_to_spool(self, tag_uid, spool_id, spool_index, tray_uuid=""):
        """v4: write tray_uuid to lot_nr instead of extra.rfid_tag_uid.

        Kept for compatibility but callers should prefer _enroll_lot_nr directly.
        """
        if tray_uuid:
            self._enroll_lot_nr(spool_id, tray_uuid, spool_index, reason="bind_uid")
        else:
            self.log(
                f"BIND_UID_SKIP spool_id={spool_id} tag_uid={tag_uid} reason=no_tray_uuid",
                level="WARNING",
            )

    def _manual_enroll(self, slot, spool_id):
        """v4: manual enrollment writes tray_uuid to lot_nr + moves location."""
        tray = self.get_state(self._tray_entity_by_slot[slot], attribute="all") or {}
        attrs = tray.get("attributes", {}) if isinstance(tray, dict) else {}
        raw_tag = attrs.get("tag_uid")
        tag_uid = self._canonicalize_tag_uid(raw_tag)
        if not tag_uid:
            raise RuntimeError("tray tag_uid is empty/zero")
        raw_tray_uuid = str(attrs.get("tray_uuid") or "").strip().replace(" ", "").replace("-", "").upper()
        enroll_tray_uuid = raw_tray_uuid if (raw_tray_uuid and raw_tray_uuid != "0" * len(raw_tray_uuid)) else ""
        if not enroll_tray_uuid:
            raise RuntimeError("tray_uuid is empty/zero — cannot enroll via lot_nr")

        spools = self._spoolman_get("/api/v1/spool?limit=1000")
        if isinstance(spools, dict) and "items" in spools:
            spools = spools.get("items", [])
        target = None
        for row in spools:
            row_id = self._safe_int(row.get("id"), 0)
            if row_id == spool_id:
                target = row
            existing_lot = str(row.get("lot_nr") or "").strip()
            if existing_lot == enroll_tray_uuid and row_id != spool_id:
                raise RuntimeError(f"tray_uuid already bound to different spool_id={row_id}")
        if not target:
            target = self._spoolman_get(f"/api/v1/spool/{spool_id}")
        existing_lot = str(target.get("lot_nr") or "").strip() if isinstance(target, dict) else ""
        if existing_lot and existing_lot != enroll_tray_uuid:
            raise RuntimeError(f"lot_nr conflict on spool_id={spool_id} existing={existing_lot}")

        spool_index = {self._safe_int(s.get("id"), 0): s for s in spools}
        self._enroll_lot_nr(spool_id, enroll_tray_uuid, spool_index, reason="manual_enroll")
        self._pending_lot_nr_writes[spool_id] = enroll_tray_uuid
        self._clear_previous_occupant_guarded(slot, spool_id, spool_index)
        self._patch_spool_fields(spool_id, {"location": self._canonical_location_by_slot[slot]})
        self.log(
            f"MANUAL_ENROLL_LOT_NR_WRITTEN slot={slot} spool_id={spool_id} tray_uuid={enroll_tray_uuid}",
            level="INFO",
        )
        self._notify(
            "RFID Manual Enroll Applied",
            f"slot={slot} spool_id={spool_id} tray_uuid={enroll_tray_uuid}",
            notification_id=f"rfid_manual_enroll_slot_{slot}",
        )

    def _try_filament_id_match(self, spools, fid_raw, slot, spool_index, nonrfid_sig,
                               tray_meta, tray, previous_helper_spool_id,
                               t, tray_empty, tray_state_str, status_only, validation_mode):
        """Step 3: filament_id exact match. Returns resolved spool_id or None."""
        fid = str(fid_raw or "").strip()
        if not fid or is_generic_filament_id(fid):
            return None
        fid_lower = fid.lower()
        has_external_id = False
        candidates = []
        for spool in spools:
            spool_id = self._safe_int(spool.get("id"), 0)
            if spool_id <= 0:
                continue
            if self._extract_spool_uid(spool):
                continue
            location = str(spool.get("location", "")).strip().lower()
            if location != "shelf":
                continue
            filament = spool.get("filament", {}) if isinstance(spool.get("filament", {}), dict) else {}
            ext_id = str(filament.get("external_id", "") or "").strip()
            if ext_id:
                has_external_id = True
                if ext_id.lower() == fid_lower:
                    candidates.append(spool)
        if not has_external_id:
            self.log(f"NONRFID_FILAMENT_ID_NO_MATCH slot={slot} filament_id={fid} reason=no_external_ids_populated", level="DEBUG")
            return None
        if len(candidates) == 0:
            self.log(f"NONRFID_FILAMENT_ID_NO_MATCH slot={slot} filament_id={fid} reason=no_matching_external_id", level="DEBUG")
            return None
        if len(candidates) > 1:
            candidate_dicts = [s for s in candidates if self._safe_float(s.get("remaining_weight"), -1) > 0]
            if candidate_dicts:
                winner_id, _ = tiebreak_choose_spool(candidate_dicts, strict_mode=False)
                if winner_id is not None:
                    candidates = [s for s in candidates if self._safe_int(s.get("id"), 0) == winner_id]
        if len(candidates) != 1:
            self.log(f"NONRFID_FILAMENT_ID_NO_MATCH slot={slot} filament_id={fid} reason=ambiguous_{len(candidates)}_candidates", level="DEBUG")
            return None
        resolved = self._safe_int(candidates[0].get("id"), 0)
        self.log(f"NONRFID_FILAMENT_ID_MATCH slot={slot} filament_id={fid} spool_id={resolved}", level="INFO")
        if not status_only:
            self._force_location_and_helpers(
                slot, resolved, "", source="nonrfid_filament_id_match",
                tray_meta=tray_meta, tray_state=tray.get("state", ""), tray_identity=nonrfid_sig,
                previous_helper_spool_id=previous_helper_spool_id,
                spool_index=spool_index, t=t, tray_empty=tray_empty, tray_state_str=tray_state_str,
            )
        t["decision"], t["reason"], t["action"] = "NON_RFID", "nonrfid_filament_id_match", "nonrfid_filament_id_match"
        t["final_spool_id"], t["selected_spool_id"] = resolved, resolved
        t["final_location"] = self._canonical_location_by_slot.get(slot, "")
        self._set_helper(f"input_text.ams_slot_{slot}_status", STATUS_OK_NONRFID)
        self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", "")
        self._log_slot_status_change(slot, STATUS_OK_NONRFID, "", resolved, tray_meta)
        self._record_decision(slot, "nonrfid_filament_id_match", {"resolved_spool_id": resolved, "filament_id": fid})
        # v4: lot_nr enrollment for filament_id match
        lot_sig_conv = self._build_lot_sig(tray_meta)
        if lot_sig_conv and not status_only:
            self._enroll_lot_nr(resolved, lot_sig_conv, spool_index, reason="nonrfid_filament_id_match")
        return resolved

    def _find_tier2_candidates(self, slot, tag_uid, tray_meta, spool_index, tray_uuid=""):
        """Tier 2: RFID spools at AMS slot locations where that slot's tray is currently empty.

        v4: primary match by lot_nr == tray_uuid, fallback to extra.rfid_tag_uid.
        """
        if not tag_uid and not tray_uuid:
            return []
        slot_tag = _normalize_rfid_tag_uid(tag_uid)

        candidates = []
        for other_slot, other_loc in self._canonical_location_by_slot.items():
            if other_slot == slot:
                continue
            other_entity = self._tray_entity_by_slot.get(other_slot)
            if not other_entity:
                continue
            other_tray = self.get_state(other_entity, attribute="all") or {}
            other_state = str(other_tray.get("state", "") if isinstance(other_tray, dict) else other_tray or "").strip().lower()
            if other_state != "empty":
                continue
            norm_loc = self._normalize_location(other_loc)
            for spool_id, spool in spool_index.items():
                if spool_id <= 0:
                    continue
                spool_loc = self._normalize_location(str(spool.get("location", "")).strip())
                if spool_loc != norm_loc:
                    continue
                # v4: primary lot_nr match, then legacy extra fallback
                matched = False
                if tray_uuid:
                    spool_lot = str(spool.get("lot_nr") or "").strip()
                    if spool_lot == tray_uuid:
                        matched = True
                if not matched and slot_tag:
                    spool_uid = self._extract_spool_uid(spool)
                    if spool_uid == slot_tag:
                        matched = True
                if not matched:
                    continue
                candidates.append(spool)
                self.log(
                    f"TIER2_CANDIDATE slot={slot} source_slot={other_slot} spool_id={spool_id} uid_match=True",
                    level="INFO",
                )

        filtered = []
        for spool in candidates:
            spool_id = self._safe_int(spool.get("id"), 0)
            excluded = False
            for other_slot in range(1, 7):
                if other_slot == slot:
                    continue
                ot_entity = self._tray_entity_by_slot[other_slot]
                ot_tray = self.get_state(ot_entity, attribute="all") or {}
                ot_state = str(ot_tray.get("state", "") if isinstance(ot_tray, dict) else ot_tray or "").strip().lower()
                if ot_state != "empty":
                    other_spool_id = self._safe_int(
                        self._get_helper_state(f"input_text.ams_slot_{other_slot}_spool_id"), 0
                    )
                    if other_spool_id > 0 and other_spool_id == spool_id:
                        self.log(
                            f"TIER2_EXCLUDED slot={slot} spool_id={spool_id} reason=active_in_other_slot other_slot={other_slot}",
                            level="INFO",
                        )
                        excluded = True
                        break
            if not excluded:
                filtered.append(spool)
        return filtered

    def _find_deterministic_candidates(self, spools, tray_meta, slot):
        candidates = []
        excluded_new_ids = []
        bambu_excluded = 0
        tray_color_hex = tray_meta.get("color_hex", "")
        for spool in spools:
            spool_id = self._safe_int(spool.get("id"), 0)
            if spool_id <= 0:
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "invalid_spool_id"})
                continue
            if self._extract_spool_uid(spool):
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "already_has_rfid"})
                continue
            location = str(spool.get("location", "")).strip().lower()
            if location == LOCATION_EMPTY.lower():
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "location_empty"})
                continue
            if location == "new":
                excluded_new_ids.append(spool_id)
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "location_new"})
                continue
            if location != "shelf" and not (location.startswith("ams")):
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "location_not_shelf_unknown"})
                continue

            self.log(
                f"RFID_ELIGIBLE_LOCATION slot={slot} spool_id={spool_id} location={spool.get('location', '')}",
                level="DEBUG",
            )
            filament = spool.get("filament", {}) if isinstance(spool.get("filament", {}), dict) else {}
            spool_fid = str(filament.get("external_id", "") or "").strip()
            if _is_bambu_vendor(spool) and is_generic_filament_id(spool_fid):
                bambu_excluded += 1
                self.log(f"NONRFID_BAMBU_EXCLUDED slot={slot} spool_id={spool_id} reason=bambu_generic_sentinel", level="DEBUG")
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "bambu_generic_sentinel"})
                continue
            lot_nr = str(spool.get("lot_nr") or "").strip()
            if _is_lot_nr_uuid(lot_nr):
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "lot_nr_uuid_rfid_enrolled"})
                continue

            spool_material = self._normalize_material(filament.get("material", ""))
            tray_material = self._normalize_material(tray_meta.get("type", ""))
            if not spool_material or spool_material != tray_material:
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "material_mismatch"})
                continue

            candidates.append(spool)
            self._record_decision(slot, "candidate_accept", {"spool_id": spool_id, "reason": "material_match"})

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

        # Color fuzzy tiebreaker: when multiple candidates remain, prefer closer color
        if len(candidates) > 1 and tray_color_hex:
            scored = []
            for spool in candidates:
                filament = spool.get("filament", {}) if isinstance(spool.get("filament", {}), dict) else {}
                spool_hex = str(filament.get("color_hex", "") or "")
                dist = _color_distance(tray_color_hex, spool_hex)
                scored.append((dist, spool))
            scored.sort(key=lambda x: x[0])
            best_dist = scored[0][0]
            if best_dist <= NONRFID_COLOR_TOLERANCE:
                within = [s for d, s in scored if d <= NONRFID_COLOR_TOLERANCE]
                if len(within) == 1:
                    selected_id = self._safe_int(within[0].get("id"), 0)
                    self.log(
                        f"NONRFID_COLOR_TIEBREAK slot={slot} candidates={len(candidates)} selected_spool_id={selected_id} distance={best_dist:.1f}",
                        level="INFO",
                    )
                    candidates = within

        ineligible_new_count = len(excluded_new_ids)
        return ([self._safe_int(s.get("id"), 0) for s in candidates], ineligible_new_count)

    def _find_deterministic_candidates_new_only(self, spools, tray_meta, slot):
        """PHASE_2_6: Candidates with location New only (same vendor+material rules as Shelf). Returns (candidate_ids,)."""
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
            filament = spool.get("filament", {}) if isinstance(spool.get("filament", {}), dict) else {}
            spool_fid = str(filament.get("external_id", "") or "").strip()
            if _is_bambu_vendor(spool) and is_generic_filament_id(spool_fid):
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "bambu_generic_sentinel"})
                continue
            if _is_lot_nr_uuid(str(spool.get("lot_nr") or "").strip()):
                self._record_decision(slot, "candidate_reject", {"spool_id": spool_id, "reason": "lot_nr_uuid_rfid_enrolled"})
                continue
            spool_material = self._normalize_material(filament.get("material", ""))
            tray_material = self._normalize_material(tray_meta.get("type", ""))
            if not spool_material or spool_material != tray_material:
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

    def _converge_lot_nr(self, slot, resolved_spool_id, tray_meta, spool_index, tray_uuid="", tag_uid=""):
        """v4 lot_nr convergence: enroll identity on resolved bind.

        RFID: writes tray_uuid to lot_nr.
        Non-RFID: writes type|filament_id|color_hex sig to lot_nr.
        Idempotent via _enroll_lot_nr (refuses overwrite of existing different value).
        Truth guard: refuse to write when tray material != spool material.
        """
        if not resolved_spool_id:
            return
        spool = spool_index.get(resolved_spool_id) if isinstance(spool_index, dict) else None
        if isinstance(spool, dict) and tray_meta:
            tray_type = self._normalize_material(tray_meta.get("type", ""))
            spool_mat = self._normalize_material((spool.get("filament") or {}).get("material") or spool.get("filament_id") or "")
            if tray_type and spool_mat and tray_type != spool_mat:
                self.log(
                    f"CONVERGE_LOT_NR_SKIP slot={slot} spool_id={resolved_spool_id} tray_type={tray_type} spool_material={spool_mat} (material mismatch)",
                    level="WARNING",
                )
                return
        if tray_uuid:
            self._enroll_lot_nr(resolved_spool_id, tray_uuid, spool_index, reason="converge_rfid")
        else:
            lot_sig = self._build_lot_sig(tray_meta)
            if lot_sig:
                self._enroll_lot_nr(resolved_spool_id, lot_sig, spool_index, reason="converge_nonrfid")

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
            "1) Run script.filament_iq_rfid_manual_enroll_tag_to_spool with slot + spool_id (spool at Shelf or AMS), OR\n"
            f"2) Create spool in Spoolman and enroll, then press {self._reconcile_button_entity}."
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
            "1) Run script.filament_iq_rfid_manual_enroll_tag_to_spool with slot + spool_id, OR\n"
            f"2) Press {self._reconcile_button_entity}."
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

    def _clear_legacy_signatures(self):
        """One-time cleanup of stored NONRFID| format tray signatures from pre-v3 runs."""
        for slot in self._physical_ams_slots:
            entity_id = f"input_text.ams_slot_{slot}_tray_signature"
            try:
                val = self.get_state(entity_id) or ""
                if val.startswith("NONRFID|"):
                    self._set_helper(entity_id, "")
                    self.log(
                        f"LEGACY_SIGNATURE_CLEARED slot={slot} old_value={val!r}",
                        level="INFO",
                    )
            except Exception as exc:
                self.log(
                    f"LEGACY_SIGNATURE_CLEAR_ERROR slot={slot} exc={exc}",
                    level="WARNING",
                )

    def _is_all_zero_identity(self, tag_uid, tray_uuid):
        """True when tag_uid and tray_uuid are both empty or all-zero (non-RFID sensors)."""
        tag_str = str(tag_uid or "").strip().replace(" ", "").replace('"', "").lower()
        tray_str = str(tray_uuid or "").strip().replace(" ", "").replace("-", "").lower()
        return (not tag_str or tag_str == "0000000000000000") and (
            not tray_str or tray_str == "00000000000000000000000000000000"
        )

    def _get_tray_identity(self, attrs, tag_uid, state_str=""):
        """Tray identity: tray_uuid (non-zero) > tag_uid (non-zero) > _build_tray_signature fallback."""
        raw_tray = (attrs or {}).get("tray_uuid")
        tray_str = str(raw_tray or "").strip().replace(" ", "").replace("-", "").upper()
        if tray_str and tray_str != "0" * len(tray_str):
            return tray_str
        tag_str = self._norm_tray_identity_tag(tag_uid)
        if tag_str and tag_str != "0" * len(tag_str):
            return tag_str
        tray_meta = self._tray_meta(attrs or {}, state_str)
        return self._build_tray_signature(tray_meta, state_str, tag_uid)

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

    def _build_lot_sig(self, tray_meta):
        """Build lot_nr identity sig for non-RFID spools: type|filament_id|color_hex (lowercase).

        Written to Spoolman lot_nr. Distinct from _build_tray_signature which
        is the internal HA helper format (includes name and tag_uid).
        Returns empty string if any required field is missing or filament_id is generic.
        """
        typ = (str(tray_meta.get("type", "") or "").strip()).lower()
        fid = (str(tray_meta.get("filament_id", "") or "").strip()).lower()
        hex_ = (str(tray_meta.get("color_hex", "") or "").strip().replace("#", "").lower())
        if len(hex_) == 8:
            hex_ = hex_[:6]
        if not typ or not fid or not hex_:
            return ""
        if is_generic_filament_id(fid):
            return ""
        return f"{typ}|{fid}|{hex_}"[:255]

    def _build_lot_sig_for_lookup(self, tray_meta):
        """Like _build_lot_sig but allows generic filament IDs.

        Used for lot_nr index lookup where even generic filaments may have
        been previously enrolled. Not used for writing new lot_nr values.
        """
        typ = (str(tray_meta.get("type", "") or "").strip()).lower()
        fid = (str(tray_meta.get("filament_id", "") or "").strip()).lower()
        hex_ = (str(tray_meta.get("color_hex", "") or "").strip().replace("#", "").lower())
        if len(hex_) == 8:
            hex_ = hex_[:6]
        if not typ or not fid or not hex_:
            return ""
        return f"{typ}|{fid}|{hex_}"[:255]

    def _nonrfid_tray_matches_bound_spool(self, tray_meta, helper_spool_id, spool_index):
        """True if tray's current identity matches the bound spool; False if swapped (trigger rematch).
        Compares tray lot_sig to spool lot_nr when spool has lot_nr; else compares material + color_hex."""
        spool_obj = spool_index.get(helper_spool_id) or {}
        existing_lot_nr = str(spool_obj.get("lot_nr") or "").strip()
        tray_lot_sig = self._build_lot_sig_for_lookup(tray_meta)
        if _is_lot_nr_uuid(existing_lot_nr):
            return False  # RFID-enrolled spool cannot match non-RFID tray
        if existing_lot_nr and tray_lot_sig:
            return existing_lot_nr == tray_lot_sig
        # Spool has no lot_nr yet: compare material + color_hex (normalize material: PLA+ -> PLA, etc.)
        tray_type_norm = self._normalize_material(tray_meta.get("type", ""))
        tray_hex = (str(tray_meta.get("color_hex", "") or "").strip().replace("#", "").lower())
        if len(tray_hex) == 8:
            tray_hex = tray_hex[:6]
        filament = spool_obj.get("filament") or {}
        spool_mat = self._normalize_material(filament.get("material", ""))
        spool_hex = (str(filament.get("color_hex", "") or "").strip().replace("#", "").lower())
        if len(spool_hex) == 8:
            spool_hex = spool_hex[:6]
        return bool(tray_type_norm and spool_mat and tray_type_norm == spool_mat and tray_hex == spool_hex)

    def _unenrolled_candidates_for_tray(self, tray_meta, spools, slot):
        """Spools with no lot_nr that match tray material + color_hex. Excludes spools in other AMS slots.
        Same logic as RFID sig fallback unenrolled search; used by non-RFID and RFID paths."""
        tray_type_norm = self._normalize_material(tray_meta.get("type", ""))
        tray_hex = (str(tray_meta.get("color_hex") or "").strip().replace("#", "").lower())
        if len(tray_hex) == 8:
            tray_hex = tray_hex[:6]
        unenrolled_ids = []
        for spool in spools:
            if not isinstance(spool, dict):
                continue
            lot_nr = (str(spool.get("lot_nr") or "").strip())
            if _is_lot_nr_uuid(lot_nr):
                continue  # RFID-enrolled (UUID lot_nr), never match to non-RFID tray
            if lot_nr:
                continue
            loc = str(spool.get("location", "")).strip().lower()
            if loc == "new" or loc == LOCATION_EMPTY.lower():
                continue
            if loc != "shelf" and not loc.startswith("ams"):
                continue
            filament = spool.get("filament") or {}
            spool_fid = str(filament.get("external_id", "") or "").strip()
            if _is_bambu_vendor(spool) and is_generic_filament_id(spool_fid):
                continue
            spool_mat = self._normalize_material(filament.get("material", ""))
            spool_hex = (str(filament.get("color_hex") or "").strip().replace("#", "").lower())
            if len(spool_hex) == 8:
                spool_hex = spool_hex[:6]
            if tray_type_norm and spool_mat and tray_type_norm != spool_mat:
                continue
            if tray_hex and spool_hex and tray_hex != spool_hex:
                continue
            sid = self._safe_int(spool.get("id"), 0)
            if sid > 0:
                unenrolled_ids.append(sid)
        return [c for c in unenrolled_ids if not self._is_spool_active_in_other_slot(c, slot)]

    def _is_confident_nonrfid(self, attrs, tray_state_str, filament_id=""):
        """True when tray attributes are specific enough for non-RFID auto-match (all slots)."""
        typ = str((attrs or {}).get("type", "") or "").strip()
        color = str((attrs or {}).get("color", "") or "").strip()
        if not typ or not color:
            return False
        state = str(tray_state_str or "").strip()
        fid = str(filament_id or "").strip().lower()
        if state.upper().startswith("GENERIC") and fid.endswith("99"):
            return False
        return True

    def _check_pending_confirmation(self, slot, current_sig, stored_sig):
        """Check tray signature pending confirmation. Returns (confirmed: bool, pending: bool).

        Stored format: ``PENDING:<count>:<epoch>:<signature>``
        Uses ``:`` to delimit wrapper fields since signature itself uses ``|``.
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

                if current_sig == pending_fp:
                    count += 1
                    if count >= 2 or (now - first_seen) >= 10:
                        self._set_helper(sig_helper, current_sig)
                        return True, False
                    self._set_helper(sig_helper, f"PENDING:{count}:{first_seen}:{current_sig}"[:255])
                    return False, True

            self._set_helper(sig_helper, f"PENDING:1:{now}:{current_sig}"[:255])
            return False, True

        if not stored_sig or current_sig == stored_sig:
            return True, False

        self._set_helper(sig_helper, f"PENDING:1:{now}:{current_sig}"[:255])
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

    def _patch_spool_fields(self, spool_id: int, fields: dict):
        """Plain top-level PATCH of Spoolman spool fields (lot_nr, location, etc).

        No extra-field encoding. No canonicalization. Used for v4 lot_nr writes.
        """
        path = f"/api/v1/spool/{spool_id}"
        if "location" in fields:
            fields = dict(fields)
            fields["location"] = self._normalize_location(fields["location"])
        self._spoolman_patch(path, fields)

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

    def _rfid_bind_guard_ok(self, resolved_spool_id, tag_uid, spool_index, tray_uuid=""):
        """True iff we may bind this slot to resolved_spool_id.

        v4: also passes when spool.lot_nr matches tray_uuid (no extra field needed).
        """
        if not tag_uid and not tray_uuid:
            return True
        spool = spool_index.get(resolved_spool_id) or self._spoolman_get(f"/api/v1/spool/{resolved_spool_id}")
        if not isinstance(spool, dict):
            return False
        # v4: lot_nr match is sufficient
        if tray_uuid:
            spool_lot = str(spool.get("lot_nr") or "").strip()
            if spool_lot == tray_uuid:
                return True
        # Legacy fallback: extra.rfid_tag_uid
        selected_uid = self._extract_spool_uid(spool)
        slot_tag = _normalize_rfid_tag_uid(tag_uid)
        if not slot_tag:
            return True
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

    def _truth_guard_slot_patch(self, slot, t, tray_meta, tag_uid, helper_spool_id, helper_spool_obj, tray_empty, tray_state_str, tray_uuid=""):
        """Return True if slot PATCHes are allowed.  False means truth violation detected:
        helpers/unbound_reason are already set, caller must skip all Spoolman writes and continue."""
        norm_tag = _normalize_rfid_tag_uid(tag_uid)
        rfid_visible = bool(norm_tag and norm_tag != "0000000000000000")

        if rfid_visible and helper_spool_id > 0:
            # v4: lot_nr is the primary identity — compare against tray_uuid
            helper_lot_nr = str(helper_spool_obj.get("lot_nr") or "").strip() if isinstance(helper_spool_obj, dict) else ""
            if tray_uuid and helper_lot_nr:
                if helper_lot_nr == tray_uuid:
                    return True
                # lot_nr populated but doesn't match → definite mismatch
                mismatch_detail = f"tray_uuid={tray_uuid} lot_nr={helper_lot_nr}"
                self.log(
                    f"TRUTH_GUARD_BLOCK slot={slot} mode=RFID_VISIBLE reason={UNBOUND_HELPER_RFID_MISMATCH} "
                    f"helper={helper_spool_id} {mismatch_detail}",
                    level="INFO",
                )
                self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_HELPER_RFID_MISMATCH)
                t["unbound_reason"] = UNBOUND_HELPER_RFID_MISMATCH
                t["unbound_detail"] = mismatch_detail
                self._notify(
                    f"RFID Truth Guard – Slot {slot}",
                    f"Helper spool {helper_spool_id} identity does not match tray ({mismatch_detail}). Helper cleared.",
                    notification_id=f"truth_guard_rfid_mismatch_{slot}",
                )
                return False

            # Migration fallback: lot_nr empty, check legacy extra.rfid_tag_uid
            helper_uid = self._extract_spool_uid(helper_spool_obj) if isinstance(helper_spool_obj, dict) else ""
            if helper_uid:
                if helper_uid == norm_tag:
                    return True
                mismatch_detail = f"helper_uid={helper_uid} tag_uid={norm_tag}"
                self.log(
                    f"TRUTH_GUARD_BLOCK slot={slot} mode=RFID_VISIBLE reason={UNBOUND_HELPER_RFID_MISMATCH} "
                    f"helper={helper_spool_id} {mismatch_detail}",
                    level="INFO",
                )
                self._set_helper(f"input_text.ams_slot_{slot}_spool_id", "0")
                self._set_helper(f"input_text.ams_slot_{slot}_expected_spool_id", "0")
                self._set_helper(f"input_text.ams_slot_{slot}_unbound_reason", UNBOUND_HELPER_RFID_MISMATCH)
                t["unbound_reason"] = UNBOUND_HELPER_RFID_MISMATCH
                t["unbound_detail"] = mismatch_detail
                self._notify(
                    f"RFID Truth Guard – Slot {slot}",
                    f"Helper spool {helper_spool_id} identity does not match tray ({mismatch_detail}). Helper cleared.",
                    notification_id=f"truth_guard_rfid_mismatch_{slot}",
                )
                return False

        if not rfid_visible and not tray_empty and helper_spool_id > 0 and isinstance(helper_spool_obj, dict):
            filament = helper_spool_obj.get("filament") if isinstance(helper_spool_obj.get("filament"), dict) else {}
            spool_material = self._normalize_material(filament.get("material", ""))
            tray_type = self._normalize_material(tray_meta.get("type", "") if tray_meta else "")
            if spool_material and tray_type and spool_material != tray_type:
                # Check bound invariant: if user explicitly assigned this spool,
                # warn but do NOT clear — respect the manual binding.
                helper_expected = self._safe_int(
                    self._get_helper_state(f"input_text.ams_slot_{slot}_expected_spool_id"), 0
                )
                if helper_expected > 0 and helper_expected == helper_spool_id:
                    self.log(
                        f"TRUTH_GUARD_MATERIAL_WARN_ONLY slot={slot} "
                        f"helper={helper_spool_id} tray_type={tray_type} spool_mat={spool_material} "
                        f"— bound invariant holds (expected={helper_expected}), preserving manual bind",
                        level="WARNING",
                    )
                    self._notify(
                        f"Material Truth Guard (warn) – Slot {slot}",
                        f"Spool {helper_spool_id} material ({spool_material}) differs from tray type ({tray_type}), "
                        f"but binding preserved because it was manually assigned.",
                        notification_id=f"truth_guard_material_warn_{slot}",
                    )
                    return True  # allow bind to proceed

                # No bound invariant — this is likely an auto-match error. Clear helpers.
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

    def _normalize_material(self, material: str) -> str:
        """Normalize material for comparison: strip, upper; PLA+ -> PLA, PETG-CF -> PETG, ABS* -> ABS."""
        s = str(material or "").strip().upper()
        if not s:
            return s
        if s.startswith("PLA"):
            return "PLA"
        if s.startswith("PETG"):
            return "PETG"
        if s.startswith("ABS"):
            return "ABS"
        return s

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
        if DomainException is not None:
            try:
                state_raw = self.get_state(entity_id)
            except DomainException as e:
                if not self._domain_exception_class_logged:
                    self.log(
                        f"domain not available (first occurrence) exception_class={type(e).__module__}.{type(e).__name__}",
                        level="WARNING",
                    )
                    self._domain_exception_class_logged = True
                self._record_no_write(entity_id, "domain_not_available", {"entity_id": entity_id})
                return
        else:
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
            self._record_no_write(entity_id, "helper_already_equal", {"entity_id": entity_id, "value": next_value})
            return

        # Route by entity domain: input_text.* -> input_text/set_value, text.* -> text/set_value.
        if entity_id.startswith("input_text."):
            if DomainException is not None:
                try:
                    self.call_service("input_text/set_value", entity_id=entity_id, value=next_value)
                except DomainException as e:
                    if not self._domain_exception_class_logged:
                        self.log(
                            f"domain not available (first occurrence) exception_class={type(e).__module__}.{type(e).__name__}",
                            level="WARNING",
                        )
                        self._domain_exception_class_logged = True
                    self._record_no_write(entity_id, "domain_not_available", {"entity_id": entity_id})
                    return
            else:
                self.call_service("input_text/set_value", entity_id=entity_id, value=next_value)
            if "unbound_reason" in entity_id:
                self.log(f"_SET_HELPER_WROTE entity_id={entity_id} service=input_text/set_value value={next_value}", level="INFO")
            self._record_write("ha_helper_set", {"entity_id": entity_id, "value": next_value})
            return
        if entity_id.startswith("text."):
            if DomainException is not None:
                try:
                    self.call_service("text/set_value", entity_id=entity_id, value=next_value)
                except DomainException as e:
                    if not self._domain_exception_class_logged:
                        self.log(
                            f"domain not available (first occurrence) exception_class={type(e).__module__}.{type(e).__name__}",
                            level="WARNING",
                        )
                        self._domain_exception_class_logged = True
                    self._record_no_write(entity_id, "domain_not_available", {"entity_id": entity_id})
                    return
            else:
                self.call_service("text/set_value", entity_id=entity_id, value=next_value)
            if "unbound_reason" in entity_id:
                self.log(f"_SET_HELPER_WROTE entity_id={entity_id} service=text/set_value value={next_value}", level="INFO")
            self._record_write("ha_helper_set", {"entity_id": entity_id, "value": next_value})
            return
        raise ValueError(f"_set_helper: unsupported entity domain for entity_id={entity_id}")

    _LAST_MAPPING_JSON_MAX = 255

    def write_last_mapping_json(self, reason, mapping):
        """Write compact JSON to last_mapping_json_entity. Always <= 255 chars."""
        ts = datetime.datetime.now().isoformat()[:19]
        out = json.dumps({"ts": ts, "reason": reason, "mapping": mapping}, separators=(",", ":"))
        if len(out) <= self._LAST_MAPPING_JSON_MAX:
            self._set_helper(self._last_mapping_json_entity, out)
            return
        out = json.dumps({"reason": reason[:32], "mapping": mapping}, separators=(",", ":"))
        if len(out) <= self._LAST_MAPPING_JSON_MAX:
            self._set_helper(self._last_mapping_json_entity, out)
            return
        out = json.dumps({"mapping": mapping}, separators=(",", ":"))
        if len(out) <= self._LAST_MAPPING_JSON_MAX:
            self._set_helper(self._last_mapping_json_entity, out)
            return
        self._set_helper(self._last_mapping_json_entity, out[:self._LAST_MAPPING_JSON_MAX])

    def _apply_unbound_reason(self, slot, t, tray_meta, tag_uid, tray_empty, tray_state_str, tray_uuid=""):
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
            f"UNBOUND_REASON slot={slot} reason={reason} tag_uid={tag_uid or ''} tray_uuid={tray_uuid or ''} detail={detail}",
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

    def _get_helper_state(self, entity_id):
        """Read helper state using attribute='all' to bypass AppDaemon cache bug.

        Plain get_state(entity_id) can return stale/wrong values after HA restart.
        get_state(entity_id, attribute='all') returns the correct full dict.
        When attribute='all' is missing (e.g. test mocks), fall back to plain get_state.
        """
        try:
            full = self.get_state(entity_id, attribute="all")
            if full is None:
                return self.get_state(entity_id)
            if isinstance(full, dict):
                return full.get("state")
            return full
        except Exception:
            return None

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
            "helper_spool_id": str(self._get_helper_state(f"input_text.ams_slot_{slot}_spool_id") or ""),
            "helper_expected_spool_id": str(self._get_helper_state(f"input_text.ams_slot_{slot}_expected_spool_id") or ""),
            "helper_status": str(self._get_helper_state(f"input_text.ams_slot_{slot}_status") or ""),
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
