"""AMS Print Usage Sync — writes filament consumption to Spoolman after each print.

Entity naming: sensor.{prefix}_{sensor_name} where prefix = _build_entity_prefix().
See base.py for the pattern (printer_model + printer_serial lowercased).

Two write paths only — no estimation:
  Path A (RFID delta):  consumption = start_g - end_g from fuel gauge snapshots.
  Path B (3MF match):   slicer-exact per-filament consumption matched to a bound slot.
Slots with neither RFID delta nor 3MF match are logged and skipped (USAGE_NO_EVIDENCE).

Tray tracking:  AppDaemon listens to tray active attribute for diagnostics/logging.

Slot-to-spool mapping: input_text.ams_slot_{slot}_spool_id (reconciler-owned, read-only).
Spoolman write:         PUT /api/v1/spool/{id}/use {"use_weight": grams}
Dedup:                  job_key set persisted to disk (capped at 50 entries).
"""

import datetime
import json
import os
import pathlib
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

import hassapi as hass

from .base import FilamentIQBase, build_slot_mappings

try:
    from .threemf_parser import (
        ftps_connect,
        ftps_download_3mf,
        ftps_download_native,
        ftps_list_cache,
        ftps_list_cache_native,
        find_best_3mf,
        match_filaments_to_slots,
        normalize_color,
        normalize_material,
        parse_3mf_filaments,
        parse_lot_nr_color,
    )
    THREEMF_AVAILABLE = True
except ImportError:
    THREEMF_AVAILABLE = False

# Path next to this app so it works under /config/appdaemon/apps or /addon_configs/.../apps
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_JOBS_PATH = os.path.join(_APP_DIR, "data", "seen_job_keys.json")

MAX_SEEN_JOBS = 50
ACTIVE_PRINT_FILE = pathlib.Path(_APP_DIR) / "data" / "active_print.json"
PRINT_HISTORY_DIR = pathlib.Path(_APP_DIR) / "data" / "print_history"
PRINT_HISTORY_MAX = 50

# Notification display labels
_METHOD_LABELS = {
    "rfid_delta":          "RFID sensor",
    "rfid_delta_depleted": "RFID sensor (depleted)",
    "3mf":                 "Slicer data",
    "3mf_depleted":        "Slicer data (depleted)",
    "depleted_nonrfid":    "Depleted",
    "no_evidence":         "—",
}

_CONFIDENCE_SYMBOLS = {
    "high":   " \u2713",
    "medium": " ~",
    "low":    " ?",
    "none":   "",
}

# RFID weight reconciler constants
TRAY_WEIGHT_MIN_G = 50.0    # Bambu AMS Lite spools are 250g; 50g gives safe headroom
TRAY_WEIGHT_MAX_G = 2000.0  # Largest Bambu spool is 1000g; 2000g catches factory/clone errors
RECONCILE_MIN_DELTA_G = 5.0 # remain% is integer 0-100; on 1000g spool, resolution = 10g

# tag_uid values that indicate non-RFID (no chip or empty)
_INVALID_TAG_UIDS = frozenset({"", "0000000000000000", "unknown", "unavailable"})


class AmsPrintUsageSync(FilamentIQBase):

    def initialize(self):
        # Validate required config first
        self._validate_config(
            required_keys=["spoolman_url", "printer_serial"],
            typed_keys={
                "max_consumption_g": (float, 1000.0),
                "min_consumption_g": (float, 2.0),
                "min_tray_active_seconds": (float, 10.0),
                "printer_ftps_port": (int, 990),
                "dry_run": (bool, False),
            },
            range_keys={
                "max_consumption_g": (1.0, None),
                "min_consumption_g": (0.0, None),
                "min_tray_active_seconds": (0.0, None),
                "printer_ftps_port": (1, 65535),
            },
        )
        self._check_spoolman_connectivity()

        self.enabled = bool(self.args.get("enabled", True))
        self.spoolman_base_url = str(
            self.args.get("spoolman_url", self.args.get("spoolman_base_url", ""))
        ).rstrip("/")
        self.dry_run = bool(self.args.get("dry_run", False))
        self.min_consumption_g = float(self.args.get("min_consumption_g", 2))
        self.max_consumption_g = float(self.args.get("max_consumption_g", 1000))
        self.auto_empty_spools = bool(self.args.get("auto_empty_spools", False))
        self.min_tray_active_seconds = float(self.args.get("min_tray_active_seconds", 10))
        self._seen_job_keys = self._load_seen_job_keys()
        self._ensure_data_dir()

        # Slot mappings from config (printer_model, printer_serial, ams_units)
        prefix = self._build_entity_prefix()
        ams_units = self.args.get("ams_units")
        (
            self._tray_entity_by_slot,
            self._slot_by_tray_entity,
            self._ams_tray_to_slot,
            _,
        ) = build_slot_mappings(prefix, ams_units)

        self._active_tray_entity = f"sensor.{prefix}_active_tray"
        self._print_status_entity = f"sensor.{prefix}_print_status"
        self._task_name_entity = f"sensor.{prefix}_task_name"
        self._print_weight_entity = f"sensor.{prefix}_print_weight"
        self._trays_used_entity = str(
            self.args.get(
                "trays_used_entity",
                "input_text.filament_iq_trays_used_this_print",
            )
        ).strip()

        # Tray activity tracking (replaces HA automation p1s_record_trays_used_during_print)
        self._trays_used = set()
        self._spool_id_snapshot = {}
        self._tray_active_times = {}
        self._current_active_slot = None
        self._print_active = False
        self._rehydrated = False

        # Phase 1: Print start lifecycle (absorbs automations A, B, C)
        self._lifecycle_phase1 = bool(self.args.get("lifecycle_phase1_enabled", False))
        self._job_key = ""
        self._start_snapshot = {}  # {slot_int: grams_float}
        self._fuel_gauge_pattern = str(
            self.args.get(
                "fuel_gauge_pattern",
                "sensor.p1s_tray_{slot}_fuel_gauge_remaining",
            )
        ).strip()
        self._ams_remaining_pattern = str(
            self.args.get(
                "ams_remaining_pattern",
                "sensor.ams_slot_{slot}_remaining_g",
            )
        ).strip()
        self._print_active_entity = str(
            self.args.get(
                "print_active_entity",
                "input_boolean.filament_iq_print_active",
            )
        ).strip()
        self._job_key_entity = str(
            self.args.get(
                "job_key_entity",
                "input_text.filament_iq_active_job_key",
            )
        ).strip()
        self._start_json_entity = str(
            self.args.get(
                "start_json_entity",
                "input_text.filament_iq_start_json",
            )
        ).strip()

        # Phase 2: Print finish lifecycle (absorbs automation D)
        self._lifecycle_phase2 = bool(self.args.get("lifecycle_phase2_enabled", False))
        self._last_processed_job_key = ""
        self._end_snapshot = {}  # {slot_int: grams_float}
        # (finish_wait state removed — 3MF fetch completes before finish)

        # Phase 3: Debug logging, swap detection, rehydrate (absorbs automations E, F, G)
        self._lifecycle_phase3 = bool(self.args.get("lifecycle_phase3_enabled", False))
        self._startup_suppress_until = (
            datetime.datetime.utcnow() + datetime.timedelta(seconds=90)
            if self.args.get("lifecycle_phase3_enabled", False) else None
        )
        self._needs_reconcile_entity = str(
            self.args.get(
                "needs_reconcile_entity",
                "input_boolean.filament_iq_needs_reconcile",
            )
        ).strip()

        # RFID weight reconciler
        self._weight_reconcile_enabled = bool(
            self.args.get("weight_reconcile_enabled", True)
        )

        # 3MF parsing config
        self.printer_ip = str(self.args.get("printer_ip", ""))
        self.printer_ftps_port = int(self.args.get("printer_ftps_port", 990))
        self.access_code_entity = str(
            self.args.get(
                "access_code_entity", "input_text.bambu_printer_access_code"
            )
        )
        self.threemf_enabled = (
            bool(self.args.get("threemf_enabled", True)) and THREEMF_AVAILABLE
        )
        self.threemf_fetch_method = str(
            self.args.get("threemf_fetch_method", "native")
        ).strip().lower()
        self.spoolman_sensor_prefix = str(
            self.args.get("spoolman_sensor_prefix", "sensor.spoolman_spool_")
        ).strip()
        self._threemf_data = None
        self._threemf_filename = None

        if self.threemf_enabled:
            self.log(
                f"3MF parsing enabled  3MF_FETCH_METHOD={self.threemf_fetch_method}",
                level="INFO",
            )
        elif not THREEMF_AVAILABLE:
            self.log(
                "3MF parsing disabled — threemf_parser module not found",
                level="WARNING",
            )

        if not self.enabled:
            self.log("AmsPrintUsageSync disabled via config", level="WARNING")
            return

        # P1S_PRINT_USAGE_READY event listener removed — lifecycle runs through
        # _on_print_status_change → _on_print_finish → _do_finish directly

        self.listen_state(
            self._on_active_tray_change,
            self._active_tray_entity,
        )

        self.listen_state(
            self._on_print_status_change,
            self._print_status_entity,
        )

        # Phase 3: swap detection listeners + rehydration
        if self._lifecycle_phase3:
            for slot in sorted(self._tray_entity_by_slot.keys()):
                self.listen_state(
                    self._on_spool_id_change,
                    f"input_text.ams_slot_{slot}_spool_id",
                )
            self.listen_event(self._on_ha_start, "homeassistant_started")
            self._rehydrate_print_state()

        self.log(
            f"AmsPrintUsageSync initialized  dry_run={self.dry_run}  "
            f"min_consumption_g={self.min_consumption_g}  "
            f"spoolman={self.spoolman_base_url}",
            level="INFO",
        )

    def _get_max_slot(self):
        """Max slot number for validation (1..slot_count)."""
        return max(self._tray_entity_by_slot.keys()) if self._tray_entity_by_slot else 6

    # ── collect phase ────────────────────────────────────────────────

    def _collect_print_inputs(
        self,
        trays_used: set,
        start_snapshot: dict,
        end_snapshot: dict,
        threemf_matched_slots: dict,
        spools_cache: dict,
    ) -> list:
        """
        Build SlotInput for every bound slot in trays_used.
        All HA/Spoolman I/O happens here. Returns list[SlotInput].
        3MF data is suppressed for RFID slots — hardware delta always wins.
        Spoolman remaining is only fetched for non-RFID depleted slots.
        """
        from .consumption_engine import SlotInput
        inputs = []

        for slot in sorted(trays_used):
            spool_id = self._read_spool_id(slot)
            if spool_id <= 0:
                tray_seconds = self._summarize_tray_times().get(slot, 0)
                if tray_seconds > 60:
                    self.log(
                        f"USAGE_SKIP slot={slot} reason=UNBOUND "
                        f"tray_seconds={tray_seconds:.0f} "
                        f"DATA_LOSS — significant activity unrecorded",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"USAGE_SKIP slot={slot} reason=UNBOUND",
                        level="INFO",
                    )
                continue

            is_rfid = self._is_rfid_slot(slot)
            entity = self._tray_entity_by_slot.get(slot)
            tray_state = str(self.get_state(entity) or "").strip() if entity else ""
            tray_empty = tray_state == "Empty"
            tray_active_seconds = self._summarize_tray_times().get(slot, 0.0)

            # RFID fuel gauge readings
            start_raw = float(
                start_snapshot.get(str(slot), start_snapshot.get(slot, -1))
            )
            start_g = start_raw if start_raw >= 0 else None
            end_raw = float(
                end_snapshot.get(str(slot), end_snapshot.get(slot, -1))
            )
            end_g = end_raw if end_raw >= 0 else None

            # 3MF data — suppressed for RFID slots (hardware truth wins)
            threemf_used_g = None
            threemf_method = None
            if slot in threemf_matched_slots:
                if is_rfid:
                    self.log(
                        f"3MF_SUPPRESSED_FOR_RFID slot={slot} spool_id={spool_id} "
                        f"— RFID delta takes precedence over slicer estimate",
                        level="DEBUG",
                    )
                else:
                    threemf_used_g, threemf_method = threemf_matched_slots[slot]

            # Spoolman remaining — only for non-RFID depleted cases
            spoolman_remaining = None
            if not is_rfid and tray_empty:
                cached = spools_cache.get(spool_id)
                if cached:
                    spoolman_remaining = float(cached.get("remaining_weight") or 0)
                else:
                    fetched = self._spoolman_get(f"/api/v1/spool/{spool_id}")
                    if fetched:
                        spoolman_remaining = float(
                            fetched.get("remaining_weight") or 0
                        )

            inputs.append(SlotInput(
                slot=slot,
                spool_id=spool_id,
                is_rfid=is_rfid,
                tray_empty=tray_empty,
                tray_active_seconds=tray_active_seconds,
                start_g=start_g,
                end_g=end_g,
                threemf_used_g=threemf_used_g,
                threemf_method=threemf_method,
                spoolman_remaining=spoolman_remaining,
            ))

        return inputs

    # ── execute phase ────────────────────────────────────────────────

    def _execute_writes(self, decisions: list, job_key: str) -> tuple:
        """
        Execute Spoolman /use writes for all non-no_evidence decisions.
        Fills decision.post_write_remaining and decision.depleted in place.
        Returns (decisions, patched_count, failed_count).
        """
        patched = 0
        failed = 0

        for decision in decisions:
            if decision.method == "no_evidence":
                continue

            if self.dry_run:
                self.log(
                    f"WOULD_PATCH slot={decision.slot} spool_id={decision.spool_id} "
                    f"use_weight={decision.consumption_g:.1f} "
                    f"method={decision.method} job_key={job_key}",
                    level="INFO",
                )
                patched += 1
                continue

            result = self._spoolman_use(decision.spool_id, decision.consumption_g)
            if result:
                decision.post_write_remaining = float(
                    result.get("remaining_weight", 0)
                )
                decision.depleted = decision.post_write_remaining <= 0
                self.log(
                    f"USAGE_PATCHED slot={decision.slot} "
                    f"spool_id={decision.spool_id} "
                    f"consumption_g={decision.consumption_g:.2f} "
                    f"method={decision.method} "
                    f"remaining={decision.post_write_remaining:.1f} "
                    f"job_key={job_key}",
                    level="INFO",
                )
                patched += 1

                if (
                    decision.method == "depleted_nonrfid"
                    and not decision.depleted
                ):
                    try:
                        self._spoolman_patch(
                            decision.spool_id, {"location": "Empty"}
                        )
                        self.log(
                            f"NONRFID_DEPLETED_LOCATION_SET "
                            f"slot={decision.slot} "
                            f"spool_id={decision.spool_id} "
                            f"location=Empty",
                            level="INFO",
                        )
                    except Exception as e:
                        self.log(
                            f"NONRFID_DEPLETED_LOCATION_FAILED "
                            f"slot={decision.slot} "
                            f"spool_id={decision.spool_id}: {e}",
                            level="WARNING",
                        )

                if decision.depleted:
                    self._spoolman_patch(
                        decision.spool_id, {"location": "Empty"}
                    )
                    self.log(
                        f"USAGE_SPOOL_DEPLETED slot={decision.slot} "
                        f"spool_id={decision.spool_id} "
                        f"remaining={decision.post_write_remaining:.1f} "
                        f"— location → Empty",
                        level="WARNING",
                    )
                    if (
                        self.auto_empty_spools
                        and not self._is_tray_physically_present(decision.slot)
                    ):
                        self.call_service(
                            "input_text/set_value",
                            entity_id=f"input_text.ams_slot_{decision.slot}_spool_id",
                            value="0",
                        )
                        self.call_service(
                            "input_text/set_value",
                            entity_id=(
                                f"input_text.ams_slot_{decision.slot}_unbound_reason"
                            ),
                            value="UNBOUND_TRAY_EMPTY",
                        )
                        self.log(
                            f"USAGE_SLOT_UNBOUND slot={decision.slot}",
                            level="INFO",
                        )
            else:
                self.log(
                    f"USAGE_PATCH_FAILED slot={decision.slot} "
                    f"spool_id={decision.spool_id}",
                    level="ERROR",
                )
                failed += 1

        return decisions, patched, failed

    # ── print history ──────────────────────────────────────────────────

    def _write_print_history(
        self, decisions: list, task_name: str, status: str, print_weight_g: float,
    ) -> None:
        """Write per-print decision record to data/print_history/{job_key}.json."""
        if not self._job_key:
            return
        try:
            PRINT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            written = [d for d in decisions if d.method != "no_evidence"]
            record = {
                "job_key": self._job_key,
                "task_name": task_name,
                "print_status": status,
                "finished_at": datetime.datetime.utcnow().isoformat() + "Z",
                "threemf_file": self._threemf_filename,
                "slicer_estimate_g": round(print_weight_g, 1),
                "total_consumed_g": round(
                    sum(d.consumption_g for d in written), 1
                ),
                "slots": {
                    str(d.slot): {
                        "spool_id": d.spool_id,
                        "method": d.method,
                        "consumption_g": round(d.consumption_g, 2),
                        "confidence": d.confidence,
                        "post_write_remaining": (
                            round(d.post_write_remaining, 1)
                            if d.post_write_remaining is not None else None
                        ),
                        "depleted": d.depleted,
                        "skip_reason": d.skip_reason,
                    }
                    for d in decisions
                },
            }
            path = PRINT_HISTORY_DIR / f"{self._job_key}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record, indent=2))
            tmp.replace(path)
            files = sorted(
                PRINT_HISTORY_DIR.glob("*.json"),
                key=lambda f: f.stat().st_mtime,
            )
            for old in files[:-PRINT_HISTORY_MAX]:
                old.unlink(missing_ok=True)
            self.log(
                f"PRINT_HISTORY_WRITTEN job_key={self._job_key}",
                level="DEBUG",
            )
        except Exception as exc:
            self.log(f"PRINT_HISTORY_WRITE_FAILED: {exc}", level="WARNING")

    # ── notification ───────────────────────────────────────────────────

    def _send_notification(
        self, decisions: list, task_name: str, status: str,
        print_weight_g: float, job_key: str,
    ) -> None:
        """Build and send print completion notification."""
        _NOTIFY_STATES = {
            "finish", "finished", "completed",
            "failed", "error", "cancelled", "canceled",
        }
        if status not in _NOTIFY_STATES:
            return

        if status in ("finish", "finished", "completed"):
            title = "\u2705 Print Complete"
        elif status in ("failed", "error"):
            title = "\u274c Print Failed"
        else:
            title = "\u26a0\ufe0f Print Cancelled"

        if getattr(self, "_print_start_time", None) is not None:
            elapsed = time.time() - self._print_start_time
            hours, remainder = divmod(int(elapsed), 3600)
            minutes = remainder // 60
            duration_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        else:
            duration_str = "unknown"

        job_label = (
            task_name.replace(".gcode.3mf", "").replace(".3mf", "").strip()
        )
        lines = [f"Job: {job_label}", f"Duration: {duration_str}", ""]

        written = [d for d in decisions if d.method != "no_evidence"]
        skipped = [d for d in decisions if d.method == "no_evidence"]

        for d in written:
            spool_name = self._get_spool_display_name(d.spool_id)
            remaining_str = (
                f"{d.post_write_remaining:.0f}g remaining"
                if d.post_write_remaining is not None
                else "remaining unknown"
            )
            confidence_sym = _CONFIDENCE_SYMBOLS.get(d.confidence, "")
            method_label = _METHOD_LABELS.get(d.method, d.method)
            depleted_tag = " \U0001faa3 DEPLETED" if d.depleted else ""
            lines.append(
                f"Slot {d.slot} ({spool_name}): "
                f"-{d.consumption_g:.1f}g \u2192 {remaining_str} "
                f"[{method_label}{confidence_sym}]{depleted_tag}"
            )

        if skipped:
            lines.append("")
            slots_str = ", ".join(f"slot {d.slot}" for d in skipped)
            lines.append(f"No data ({len(skipped)} slot(s)): {slots_str}")

        if not written:
            lines.append("No filament consumption recorded.")

        lines.append("")
        total_consumed = sum(d.consumption_g for d in written)
        lines.append(
            f"Total: {total_consumed:.1f}g | Estimate: {print_weight_g:.1f}g"
        )
        message = "\n".join(lines)

        try:
            self.call_service(
                "notify/mobile_app_jd_pixel_10xl",
                title=title,
                message=message,
            )
        except Exception as exc:
            self.log(f"USAGE_NOTIFY_FAILED: {exc}", level="WARNING")

    # ── legacy _handle_usage_event removed — lifecycle runs through
    # _on_print_status_change → _on_print_finish → _do_finish

    # ── tray activity tracking ────────────────────────────────────────

    _PAUSE_STATES = frozenset({"pause", "paused"})

    def _on_print_status_change(self, entity, attribute, old, new, kwargs):
        # Debug logging (replaces automation E)
        self.log(f"PRINT_STATUS_TRANSITION from={old} to={new}", level="DEBUG")

        if new in ("running", "printing") and old not in ("running", "printing"):
            self._trays_used = set()
            self._spool_id_snapshot = {}
            self._tray_active_times = {}
            self._current_active_slot = None
            self._print_active = True
            self._rehydrated = False
            self._seed_active_trays()
            self.log(
                f"TRAY_TRACKING_START trays_used={self._trays_used}",
                level="INFO",
            )
            if self.threemf_enabled:
                self.run_in(self._fetch_3mf_background, 10)
            if self._lifecycle_phase1:
                self._on_print_start()
            self.run_in(self._check_unbound_trays, 10)
        elif old in ("running", "printing") and new not in ("running", "printing", "pause", "paused"):
            self._print_active = False
            if self._current_active_slot is not None:
                self._close_active_segment(self._current_active_slot)
                self._current_active_slot = None
            self.log(
                f"TRAY_TRACKING_END trays_used={self._trays_used} "
                f"active_times={self._summarize_tray_times()}",
                level="INFO",
            )
            try:
                trays_str = ",".join(str(s) for s in sorted(self._trays_used))
                self.call_service(
                    "input_text/set_value",
                    entity_id=self._trays_used_entity,
                    value=trays_str,
                )
            except Exception as e:
                self.log(
                    f"TRAY_TRACKING: Failed to update HA helper: {e}",
                    level="WARNING",
                )
            if self._lifecycle_phase2:
                self._on_print_finish(new)
            elif self._lifecycle_phase1:
                self._on_print_end()

    def _resolve_active_tray_slot(self):
        try:
            ams_idx = self.get_state(self._active_tray_entity, attribute="ams_index")
            tray_idx = self.get_state(self._active_tray_entity, attribute="tray_index")
            if ams_idx is None or tray_idx is None:
                return None
            return self._ams_tray_to_slot.get((int(ams_idx), int(tray_idx)))
        except (TypeError, ValueError):
            return None

    def _seed_active_trays(self):
        state = self.get_state(self._active_tray_entity)
        if not state or state in ("none", "unknown", "unavailable"):
            return
        slot = self._resolve_active_tray_slot()
        if slot is not None:
            self._trays_used.add(slot)
            self._open_active_segment(slot)
            self._current_active_slot = slot
            self.log(
                f"TRAY_TRACKING_SEED slot={slot} state={state}",
                level="INFO",
            )

    def _on_active_tray_change(self, entity, attribute, old, new, kwargs):
        if not self._print_active:
            return

        slot = self._resolve_active_tray_slot()

        if new in ("none", "unknown", "unavailable") or slot is None:
            if self._current_active_slot is not None:
                self._close_active_segment(self._current_active_slot)
                self._current_active_slot = None
            return

        if self._current_active_slot is not None and self._current_active_slot != slot:
            self._close_active_segment(self._current_active_slot)

        self._trays_used.add(slot)
        self._open_active_segment(slot)
        self._current_active_slot = slot
        if self._lifecycle_phase1:
            self._seed_slot_start_grams(slot)
        self.log(
            f"TRAY_TRACKING_ACTIVE slot={slot} trays_used={self._trays_used}",
            level="DEBUG",
        )

    def _open_active_segment(self, slot):
        if slot not in self._tray_active_times:
            self._tray_active_times[slot] = []
        segments = self._tray_active_times[slot]
        if segments and segments[-1].get("end") is None:
            return
        segments.append({"start": datetime.datetime.utcnow(), "end": None})

    def _close_active_segment(self, slot):
        segments = self._tray_active_times.get(slot, [])
        if segments and segments[-1].get("end") is None:
            segments[-1]["end"] = datetime.datetime.utcnow()

    # ── Phase 1: print start lifecycle ──────────────────────────────

    def _read_fuel_gauge(self, slot):
        """Read fuel gauge for a slot, with ams_remaining fallback. Returns grams or -1."""
        fg_entity = self._fuel_gauge_pattern.format(slot=slot)
        try:
            raw = self.get_state(fg_entity)
            if not raw or str(raw).strip().lower() in ("unavailable", "unknown", ""):
                fg = -999.0
            else:
                fg = float(raw)
        except (TypeError, ValueError):
            fg = -999.0
        if fg >= -5:
            return fg
        ams_entity = self._ams_remaining_pattern.format(slot=slot)
        try:
            ams = float(self.get_state(ams_entity) or -1)
        except (TypeError, ValueError):
            ams = -1.0
        return ams if ams >= 0 else -1.0

    def _build_start_snapshot(self):
        """Read fuel gauge for all slots, return {slot_int: grams} for slots with valid readings."""
        snapshot = {}
        for slot in sorted(self._tray_entity_by_slot.keys()):
            grams = self._read_fuel_gauge(slot)
            if grams >= 0:
                snapshot[slot] = max(0.0, round(grams, 1))
        return snapshot

    def _snapshot_to_json_dict(self, snapshot):
        """Convert {slot_int: grams} to {slot_str: grams} matching automation D's start_json format."""
        return {str(slot): grams for slot, grams in snapshot.items()}

    def _write_start_json_helper(self):
        """Write self._start_snapshot to the HA start_json helper (bridge for automation D)."""
        try:
            json_dict = self._snapshot_to_json_dict(self._start_snapshot)
            self.call_service(
                "input_text/set_value",
                entity_id=self._start_json_entity,
                value=json.dumps(json_dict),
            )
        except Exception as e:
            self.log(f"LIFECYCLE: Failed to write start_json helper: {e}", level="WARNING")

    def _on_print_start(self):
        """Phase 1 print start handler: job key, start snapshot, HA helpers."""
        self._print_start_time = time.time()
        task_name = str(self.get_state(self._task_name_entity) or "")
        self._job_key = f"{task_name.replace(' ', '_')}_{int(time.time())}"
        self._start_snapshot = self._build_start_snapshot()

        # Write HA helpers (bridge for automation D)
        try:
            self.call_service(
                "input_boolean/turn_on",
                entity_id=self._print_active_entity,
            )
        except Exception as e:
            self.log(f"LIFECYCLE: Failed to set print_active: {e}", level="WARNING")
        try:
            self.call_service(
                "input_text/set_value",
                entity_id=self._job_key_entity,
                value=self._job_key,
            )
        except Exception as e:
            self.log(f"LIFECYCLE: Failed to write job_key helper: {e}", level="WARNING")
        self._write_start_json_helper()

        self._spool_id_snapshot = {}
        for slot in range(1, 7):
            val = self.get_state(f"input_text.ams_slot_{slot}_spool_id")
            if val and str(val).isdigit() and int(val) > 0:
                self._spool_id_snapshot[slot] = int(val)
        self.log(
            f"PRINT_START_CAPTURED job_key={self._job_key} "
            f"start_snapshot={self._start_snapshot} "
            f"spool_ids={len(self._spool_id_snapshot)}",
            level="INFO",
        )
        self._persist_active_print()

    def _check_unbound_trays(self, kwargs):
        """Warn if any actively-used tray is unbound (delayed check)."""
        if not self._print_active:
            return
        if not self._trays_used:
            self.log("UNBOUND_CHECK_SKIPPED reason=no_trays_yet", level="DEBUG")
            return
        unbound = []
        for slot in sorted(self._trays_used):
            spool_id = str(
                self.get_state(f"input_text.ams_slot_{slot}_spool_id") or ""
            ).strip()
            if not spool_id or spool_id in ("0", "unknown", "unavailable"):
                reason = str(
                    self.get_state(f"input_text.ams_slot_{slot}_unbound_reason") or ""
                ).strip()
                if reason not in ("", "unknown", "unavailable", "UNBOUND_TRAY_EMPTY"):
                    unbound.append((slot, reason))
        if not unbound:
            return
        slots_str = ", ".join(f"slot {s} ({r})" for s, r in unbound)
        self.log(
            f"PRINT_UNBOUND_WARNING slots=[{slots_str}]", level="WARNING",
        )
        try:
            msg = f"Print started with unbound active slot: {slots_str}"
            self.call_service(
                "notify/mobile_app_jd_pixel_10xl",
                title="Print With Unbound Slot", message=msg,
            )
        except Exception as e:
            self.log(f"UNBOUND_WARN_NOTIFY_FAILED: {e}", level="WARNING")

    def _seed_slot_start_grams(self, slot):
        """Write-once: seed start grams for a newly-active slot during print."""
        if slot in self._start_snapshot and self._start_snapshot[slot] >= 0:
            return  # already seeded
        grams = self._read_fuel_gauge(slot)
        if grams < 0:
            return
        self._start_snapshot[slot] = max(0.0, round(grams, 1))
        self._write_start_json_helper()
        self.log(
            f"TRAY_START_SEEDED slot={slot} grams={self._start_snapshot[slot]}",
            level="INFO",
        )

    def _on_print_end(self):
        """Phase 1 print end handler: clear snapshot, set print_active off."""
        self._start_snapshot = {}
        self._job_key = ""
        try:
            self.call_service(
                "input_boolean/turn_off",
                entity_id=self._print_active_entity,
            )
        except Exception as e:
            self.log(f"LIFECYCLE: Failed to clear print_active: {e}", level="WARNING")

    # ── Phase 2: print finish lifecycle ──────────────────────────────

    _SUCCESS_STATES = frozenset({"finish"})
    _FAILED_STATES = frozenset({"failed", "error"})

    def _build_end_snapshot(self):
        """Read fuel gauge for slots present in start_snapshot. Returns {slot_int: grams}."""
        snapshot = {}
        for slot in sorted(self._start_snapshot.keys()):
            grams = self._read_fuel_gauge(slot)
            if grams >= 0:
                snapshot[slot] = max(0.0, round(grams, 1))
        return snapshot

    def _on_print_finish(self, new_status):
        """Phase 2 print finish handler: non-blocking 3MF wait, then _do_finish."""
        status = str(new_status).strip().lower()

        # Guard: no start data — let event listener handle as fallback
        if not self._job_key:
            self.log(
                "PRINT_FINISH_SKIP reason=no_job_key (AppDaemon restart mid-print?)",
                level="WARNING",
            )
            self._on_print_end()
            return
        if not self._start_snapshot:
            self.log(
                f"PRINT_FINISH_SKIP reason=no_start_snapshot job_key={self._job_key}",
                level="WARNING",
            )
            self._on_print_end()
            return

        # Dedup guard
        if self._job_key == self._last_processed_job_key:
            self.log(
                f"PRINT_FINISH_DEDUP_SKIP job_key={self._job_key}",
                level="INFO",
            )
            self._on_print_end()
            return

        # 3MF was fetched at print start — it is either cached or it is not.
        # If not in memory, attempt disk recovery before proceeding.
        if self.threemf_enabled and self._threemf_data is None:
            restored = self._load_active_print(self._job_key)
            if restored is not None:
                self._threemf_data = restored["threemf_data"]
                if restored["trays_used"]:
                    self._trays_used = restored["trays_used"]
                if restored["spool_id_snapshot"]:
                    self._spool_id_snapshot = restored["spool_id_snapshot"]
                self.log("3MF_RECOVERED_FROM_DISK", level="INFO")
            else:
                self.log(
                    "3MF_UNAVAILABLE_AT_FINISH — RFID slots unaffected, "
                    "non-RFID without Empty signal will be no_evidence",
                    level="WARNING",
                )
        self._do_finish(status)

    def _do_finish(self, status: str) -> None:
        """Orchestrate the five phases of print finish processing."""

        # ── COLLECT ──────────────────────────────────────────────────────
        self._end_snapshot = self._build_end_snapshot()
        spools_cache = self._fetch_spools_cache()
        task_name = str(self.get_state(self._task_name_entity) or "")
        try:
            print_weight_g = float(
                self.get_state(self._print_weight_entity) or 0
            )
        except (TypeError, ValueError):
            print_weight_g = 0.0

        # Filter brief tray activations
        filtered_trays = self._filter_trays_by_duration(self._trays_used)
        if filtered_trays != self._trays_used:
            removed = self._trays_used - filtered_trays
            self.log(
                f"TRAY_FILTER_REMOVED slots={removed} "
                f"reason=below_{self.min_tray_active_seconds}s_threshold",
                level="INFO",
            )
        self._trays_used = filtered_trays

        # Narrow active slots to intersection of trays_used and start_snapshot
        active_slots = self._trays_used & set(self._start_snapshot.keys())
        if active_slots != self._trays_used:
            self.log(
                f"ACTIVE_SLOTS_NARROWED from={len(self._trays_used)} "
                f"to={len(active_slots)} "
                f"trays_used={sorted(self._trays_used)}",
                level="INFO",
            )
        if not active_slots:
            if self._rehydrated:
                self.log(
                    f"USAGE_SKIP reason=NO_ACTIVE_SLOTS job_key={self._job_key} "
                    f"rehydrated=True — tray tracking lost across restart",
                    level="WARNING",
                )
            else:
                self.log(
                    f"USAGE_SKIP reason=NO_ACTIVE_SLOTS job_key={self._job_key}",
                    level="INFO",
                )
            self._on_print_end()
            self._clear_active_print()
            return
        self._trays_used = active_slots

        # Build 3MF match dict — only on success
        threemf_matched_slots = {}
        if (
            self._threemf_data
            and self.threemf_enabled
            and status in self._SUCCESS_STATES
        ):
            slot_data = self._build_slot_data(spools_cache=spools_cache)
            matches, unmatched = match_filaments_to_slots(
                self._threemf_data,
                slot_data,
                trays_used=self._trays_used or None,
            )
            for m in matches:
                threemf_matched_slots[m["slot"]] = (m["used_g"], m["method"])
                self.log(
                    f"3MF_MATCH slot={m['slot']} spool_id={m['spool_id']} "
                    f"used_g={m['used_g']:.2f} method={m['method']}",
                    level="INFO",
                )
            if unmatched:
                unmatched_total = sum(f["used_g"] for f in unmatched)
                self.log(
                    f"3MF_UNMATCHED count={len(unmatched)} "
                    f"total_g={unmatched_total:.2f}",
                    level="WARNING",
                )
        elif status not in self._SUCCESS_STATES:
            self.log(
                f"3MF_SUPPRESSED_NON_SUCCESS status={status} "
                f"job_key={self._job_key} — falling back to RFID delta only",
                level="WARNING",
            )

        self.log(
            f"PRINT_FINISH_CAPTURED job_key={self._job_key} "
            f"end_snapshot={self._end_snapshot} status={status}",
            level="INFO",
        )

        # Dedup guard
        if self._job_key and self._job_key in self._seen_job_keys:
            self.log(f"DEDUP_SKIP job_key={self._job_key}", level="INFO")
            self._on_print_end()
            return

        slot_inputs = self._collect_print_inputs(
            trays_used=self._trays_used,
            start_snapshot=self._start_snapshot,
            end_snapshot=self._end_snapshot,
            threemf_matched_slots=threemf_matched_slots,
            spools_cache=spools_cache,
        )

        if not slot_inputs:
            self.log(
                f"USAGE_SKIP reason=NO_ACTIVE_SLOTS job_key={self._job_key}",
                level="INFO",
            )
            self._on_print_end()
            self._clear_active_print()
            return

        # ── DECIDE ───────────────────────────────────────────────────────
        from .consumption_engine import decide_consumption
        all_decisions = decide_consumption(
            slot_inputs,
            min_consumption_g=self.min_consumption_g,
            max_consumption_g=self.max_consumption_g,
        )

        for d in all_decisions:
            if d.method == "no_evidence":
                self.log(
                    f"USAGE_NO_EVIDENCE slot={d.slot} spool_id={d.spool_id} "
                    f"reason={d.skip_reason}",
                    level="INFO",
                )

        # ── EXECUTE ──────────────────────────────────────────────────────
        all_decisions, patched, failed = self._execute_writes(
            all_decisions, self._job_key
        )

        # ── FINALIZE ─────────────────────────────────────────────────────
        if self._job_key and failed == 0:
            self._seen_job_keys[self._job_key] = True
            while len(self._seen_job_keys) > MAX_SEEN_JOBS:
                self._seen_job_keys.popitem(last=False)
            self._persist_seen_job_keys()

        written = [d for d in all_decisions if d.method != "no_evidence"]
        total_consumed = sum(d.consumption_g for d in written)
        self.log(
            f"USAGE_SUMMARY job_key={self._job_key} task={task_name} "
            f"status={status} patched={patched} failed={failed} "
            f"total_consumed_g={total_consumed:.1f} "
            f"slicer_estimate_g={print_weight_g:.1f}",
            level="INFO",
        )

        self._write_print_history(all_decisions, task_name, status, print_weight_g)

        # Schedule RFID reconciler (60s defer)
        _RECONCILE_DELAY = 60
        self.run_in(self._reconcile_rfid_weights_deferred, _RECONCILE_DELAY)
        self.log(
            f"RFID_WEIGHT_RECONCILE_DEFERRED job_key={self._job_key} "
            f"delay={_RECONCILE_DELAY}s",
            level="INFO",
        )

        if status not in self._FAILED_STATES:
            self._last_processed_job_key = self._job_key

        # ── NOTIFY ───────────────────────────────────────────────────────
        self._send_notification(
            all_decisions, task_name, status, print_weight_g,
            job_key=self._job_key,
        )

        self._print_start_time = None
        self._end_snapshot = {}
        self._on_print_end()
        self._clear_active_print()

    # ── Phase 3: debug logging, swap detection, rehydrate ──────────

    def _on_spool_id_change(self, entity, attribute, old, new, kwargs):
        """Log spool mapping changes during active print for diagnostics."""
        if not self._lifecycle_phase3:
            return
        if not self._print_active:
            return
        # Startup suppression
        if self._startup_suppress_until and datetime.datetime.utcnow() < self._startup_suppress_until:
            return
        # Log for diagnostics only — Bambu P1S does not support mid-print
        # spool changes, so any spool_id helper change during print is a
        # reconciler correction, not a physical swap.
        self.log(
            f"SPOOL_ID_CHANGED_DURING_PRINT entity={entity} old={old} "
            f"new={new} (no action — reconciler correction, not physical swap)",
            level="DEBUG",
        )

    def _rehydrate_print_state(self):
        """Restore print-active state if printer is mid-print (replaces automation G)."""
        try:
            current_status = str(self.get_state(self._print_status_entity) or "").strip().lower()
        except Exception:
            return
        if current_status not in ("running", "printing", "pause", "paused"):
            return
        self._print_active = True
        self.log(f"REHYDRATE_PRINT_ACTIVE status={current_status}", level="INFO")
        try:
            self.call_service(
                "input_boolean/turn_on",
                entity_id=self._print_active_entity,
            )
        except Exception as e:
            self.log(f"REHYDRATE: Failed to set print_active: {e}", level="WARNING")
        # Phase 1: rebuild start snapshot
        if self._lifecycle_phase1:
            # Try to recover from HA helper first
            recovered = False
            try:
                raw = str(self.get_state(self._start_json_entity) or "").strip()
                if raw and raw not in ("{}", "unknown", "unavailable"):
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and parsed:
                        self._start_snapshot = {int(k): float(v) for k, v in parsed.items()}
                        recovered = True
                        self.log(
                            f"REHYDRATE_START_SNAPSHOT_RECOVERED from helper: {self._start_snapshot}",
                            level="INFO",
                        )
            except Exception as e:
                self.log(f"REHYDRATE: Failed to recover start_json: {e}", level="WARNING")
            if not recovered:
                self._start_snapshot = self._build_start_snapshot()
                self._write_start_json_helper()
                self.log(
                    f"REHYDRATE_START_SNAPSHOT_REBUILT from fuel gauges: {self._start_snapshot}",
                    level="INFO",
                )
            # Seed active trays — _trays_used starts empty, populated by tray change events
            self._seed_active_trays()
            self._rehydrated = True
            self.log("REHYDRATE_FLAG_SET reason=tray_timing_invalid", level="INFO")

            # Read full job_key from HA helper — it holds the timestamp-suffixed key
            # and survives AppDaemon restarts. Only fall back to task_name if empty.
            # IMPORTANT: do NOT overwrite the helper when it already has the correct key.
            task_name = str(self.get_state(self._task_name_entity) or "")
            helper_key = str(self.get_state(self._job_key_entity) or "").strip()
            if helper_key and helper_key not in ("unknown", "unavailable"):
                self._job_key = helper_key
                self.log(
                    f"REHYDRATE_JOB_KEY_FROM_HELPER job_key={self._job_key}",
                    level="INFO",
                )
            else:
                self._job_key = task_name.replace(" ", "_")
                self.log(
                    f"REHYDRATE_JOB_KEY_FROM_TASK_NAME job_key={self._job_key}",
                    level="INFO",
                )
                try:
                    self.call_service(
                        "input_text/set_value",
                        entity_id=self._job_key_entity,
                        value=self._job_key,
                    )
                except Exception:
                    pass
            restored = self._load_active_print(self._job_key)
            if restored is not None:
                self._threemf_data = restored["threemf_data"]
                if restored["trays_used"]:
                    self._trays_used = restored["trays_used"]
                if restored["spool_id_snapshot"]:
                    self._spool_id_snapshot = restored["spool_id_snapshot"]

    def _on_ha_start(self, event_name, data, kwargs):
        """Handle HA restart while AppDaemon is running (replaces automation G)."""
        self._rehydrate_print_state()

    def _summarize_tray_times(self):
        result = {}
        for slot, segments in self._tray_active_times.items():
            total = 0.0
            for seg in segments:
                start = seg.get("start")
                end = seg.get("end")
                if start and end:
                    total += (end - start).total_seconds()
            if total > 0:
                result[slot] = round(total, 1)
        return result

    def _filter_trays_by_duration(self, trays_used):
        """Filter trays_used to only include slots with total active time >= threshold.

        Brief activations during filament load/purge sequences (< min_tray_active_seconds)
        are excluded to prevent phantom consumption tracking.
        Slots with no recorded segments (e.g. seeded at start) are always kept.
        """
        if not self._tray_active_times:
            return trays_used
        if self._rehydrated:
            self.log("TRAY_FILTER_SKIPPED reason=rehydrated_print", level="INFO")
            return trays_used
        filtered = set()
        for slot in trays_used:
            segments = self._tray_active_times.get(slot, [])
            if not segments:
                # No timing data — slot was seeded at start, keep it
                filtered.add(slot)
                continue
            total = sum(
                (s["end"] - s["start"]).total_seconds()
                for s in segments if s.get("end") and s.get("start")
            )
            if total >= self.min_tray_active_seconds:
                filtered.add(slot)
        return filtered

    def _get_access_code(self):
        code = str(self.args.get("printer_access_code", "")).strip()
        if code:
            return code
        try:
            code = str(self.get_state(self.access_code_entity) or "").strip()
            if code.lower() in ("unavailable", "unknown", "none", ""):
                code = ""
        except Exception:
            code = ""
        return code if code else None

    def _fetch_3mf_background(self, kwargs):
        """Fetch and parse a 3MF file from the printer with retry + multi-directory fallback."""
        if self.threemf_fetch_method == "native":
            return self._fetch_3mf_native(kwargs)
        return self._fetch_3mf_curl(kwargs)

    def _fetch_3mf_native(self, kwargs):
        """Fetch 3MF via ftplib — single TLS handshake for list + download."""
        attempt = kwargs.get("attempt", 1)
        max_attempts = 4
        retry_delays = [15, 45, 90]

        if attempt == 1:
            self._threemf_data = None
            self._threemf_filename = None

        access_code = self._get_access_code()
        if not access_code:
            self.log("3MF_FETCH: No access code available", level="ERROR")
            return

        task_name = str(self.get_state(self._task_name_entity) or "")

        if not self.printer_ip:
            self.log("3MF_FETCH: printer_ip not configured", level="WARNING")
            return

        self.log(
            f"3MF_FETCH: attempt {attempt}/{max_attempts} method=native "
            f"for task={task_name}",
            level="INFO",
        )

        conn = None
        try:
            conn = ftps_connect(
                self.printer_ip, access_code, self.printer_ftps_port
            )

            file_list, found_dir = ftps_list_cache_native(conn)
            if not file_list:
                if attempt < max_attempts:
                    delay = retry_delays[attempt - 1]
                    self.log(
                        f"3MF_FETCH: No .3mf files found (attempt {attempt}), "
                        f"retrying in {delay}s",
                        level="WARNING",
                    )
                    self.run_in(
                        self._fetch_3mf_background, delay, attempt=attempt + 1
                    )
                    return
                self.log(
                    "3MF_FETCH: No .3mf files found after all retries",
                    level="WARNING",
                )
                self._persist_active_print(threemf_unavailable=True)
                return

            best_file = find_best_3mf(file_list, task_name)
            if not best_file:
                self.log(
                    f"3MF_FETCH: No match for task={task_name} in {file_list}",
                    level="WARNING",
                )
                self._persist_active_print(threemf_unavailable=True)
                return

            self.log(
                f"3MF_FETCH: downloading {best_file} from {found_dir}",
                level="INFO",
            )

            with tempfile.TemporaryDirectory() as tmp_dir:
                local_path = ftps_download_native(
                    conn, found_dir, best_file,
                    os.path.join(tmp_dir, best_file),
                )
                if not local_path:
                    if attempt < max_attempts:
                        delay = retry_delays[attempt - 1]
                        self.log(
                            f"3MF_FETCH: Download failed (attempt {attempt}), "
                            f"retrying in {delay}s",
                            level="WARNING",
                        )
                        self.run_in(
                            self._fetch_3mf_background, delay,
                            attempt=attempt + 1,
                        )
                        return
                    self.log(
                        f"3MF_FETCH: Download failed for {best_file} "
                        f"after all retries",
                        level="ERROR",
                    )
                    self._persist_active_print(threemf_unavailable=True)
                    return

                filaments = parse_3mf_filaments(local_path)
                if not filaments:
                    self.log(
                        f"3MF_FETCH: No filament data in {best_file}",
                        level="WARNING",
                    )
                    self._persist_active_print(threemf_unavailable=True)
                    return

            self._threemf_data = filaments
            self._threemf_filename = best_file
            total_g = sum(f["used_g"] for f in filaments)
            self.log(
                f"3MF_PARSED file={best_file} dir={found_dir} "
                f"filaments={len(filaments)} total_g={total_g:.2f} "
                f"breakdown={[(f['index'], f['used_g'], f['color_hex'], f['material']) for f in filaments]}",
                level="INFO",
            )
            self._persist_active_print()

        except Exception as e:
            self.log(
                f"3MF_FETCH_NATIVE_ERROR attempt={attempt} error={e}",
                level="ERROR",
            )
            if attempt < max_attempts:
                delay = retry_delays[attempt - 1]
                self.log(
                    f"3MF_FETCH: Connection failed (attempt {attempt}), "
                    f"retrying in {delay}s",
                    level="WARNING",
                )
                self.run_in(
                    self._fetch_3mf_background, delay, attempt=attempt + 1
                )
            else:
                self.log(
                    "3MF_FETCH: All attempts failed (native)",
                    level="ERROR",
                )
                self._persist_active_print(threemf_unavailable=True)
        finally:
            if conn is not None:
                try:
                    conn.quit()
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _fetch_3mf_curl(self, kwargs):
        """Fetch 3MF via curl subprocesses (legacy fallback)."""
        attempt = kwargs.get("attempt", 1)
        max_attempts = 4
        retry_delays = [15, 45, 90]

        if attempt == 1:
            self._threemf_data = None
            self._threemf_filename = None

        access_code = self._get_access_code()
        if not access_code:
            self.log("3MF_FETCH: No access code available", level="ERROR")
            return

        task_name = str(self.get_state(self._task_name_entity) or "")

        if not self.printer_ip:
            self.log("3MF_FETCH: printer_ip not configured", level="WARNING")
            return

        self.log(
            f"3MF_FETCH: attempt {attempt}/{max_attempts} method=curl "
            f"for task={task_name}",
            level="INFO",
        )

        file_list, found_dir = ftps_list_cache(
            self.printer_ip, access_code, self.printer_ftps_port
        )
        if not file_list:
            if attempt < max_attempts:
                delay = retry_delays[attempt - 1]
                self.log(
                    f"3MF_FETCH: No .3mf files found (attempt {attempt}), "
                    f"retrying in {delay}s",
                    level="WARNING",
                )
                self.run_in(
                    self._fetch_3mf_background, delay, attempt=attempt + 1
                )
                return
            self.log(
                "3MF_FETCH: No .3mf files found after all retries",
                level="WARNING",
            )
            self._persist_active_print(threemf_unavailable=True)
            return

        best_file = find_best_3mf(file_list, task_name)
        if not best_file:
            self.log(
                f"3MF_FETCH: No match for task={task_name} in {file_list}",
                level="WARNING",
            )
            self._persist_active_print(threemf_unavailable=True)
            return

        self.log(
            f"3MF_FETCH: downloading {best_file} from {found_dir}",
            level="INFO",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = ftps_download_3mf(
                self.printer_ip,
                access_code,
                best_file,
                tmp_dir,
                self.printer_ftps_port,
                directory=found_dir,
            )
            if not local_path:
                if attempt < max_attempts:
                    delay = retry_delays[attempt - 1]
                    self.log(
                        f"3MF_FETCH: Download failed (attempt {attempt}), "
                        f"retrying in {delay}s",
                        level="WARNING",
                    )
                    self.run_in(
                        self._fetch_3mf_background, delay, attempt=attempt + 1
                    )
                    return
                self.log(
                    f"3MF_FETCH: Download failed for {best_file} after all retries",
                    level="ERROR",
                )
                self._persist_active_print(threemf_unavailable=True)
                return

            filaments = parse_3mf_filaments(local_path)
            if not filaments:
                self.log(
                    f"3MF_FETCH: No filament data in {best_file}",
                    level="WARNING",
                )
                self._persist_active_print(threemf_unavailable=True)
                return

        self._threemf_data = filaments
        self._threemf_filename = best_file
        total_g = sum(f["used_g"] for f in filaments)
        self.log(
            f"3MF_PARSED file={best_file} dir={found_dir} filaments={len(filaments)} "
            f"total_g={total_g:.2f} "
            f"breakdown={[(f['index'], f['used_g'], f['color_hex'], f['material']) for f in filaments]}",
            level="INFO",
        )
        self._persist_active_print()

    def _fetch_spools_cache(self):
        """Batch-fetch all spools from Spoolman. Returns {spool_id: spool_dict}."""
        try:
            raw = self._spoolman_get("/api/v1/spool?limit=1000")
            if isinstance(raw, list):
                spools = raw
            elif isinstance(raw, dict):
                spools = raw.get("items", raw.get("results", []))
            else:
                return {}
            return {int(s.get("id", 0)): s for s in spools if s.get("id")}
        except Exception as e:
            self.log(f"SPOOLMAN_BATCH_FETCH_FAILED: {e}", level="WARNING")
            return {}

    def _build_slot_data(self, spools_cache=None):
        slot_data = {}
        for slot, entity in self._tray_entity_by_slot.items():
            try:
                spool_id = self._read_spool_id(slot)

                tray_color = self.get_state(entity, attribute="color") or ""
                tray_type = self.get_state(entity, attribute="type") or ""
                color = normalize_color(tray_color)
                material = normalize_material(tray_type)

                if not color and spool_id > 0:
                    try:
                        spoolman_color = (
                            self.get_state(
                                f"{self.spoolman_sensor_prefix}{spool_id}",
                                attribute="color_hex",
                            )
                            or ""
                        )
                        color = normalize_color(spoolman_color)
                    except Exception:
                        pass
                if not material and spool_id > 0:
                    try:
                        spoolman_mat = (
                            self.get_state(
                                f"{self.spoolman_sensor_prefix}{spool_id}",
                                attribute="material",
                            )
                            or ""
                        )
                        material = normalize_material(spoolman_mat)
                    except Exception:
                        pass

                # Extract lot_nr color for fallback matching
                lot_nr_color = ""
                if spool_id > 0:
                    try:
                        cached = (spools_cache or {}).get(spool_id)
                        if cached:
                            lot_nr = str(cached.get("lot_nr", "") or "")
                            lot_nr_color = parse_lot_nr_color(lot_nr)
                    except Exception:
                        pass

                slot_data[slot] = {
                    "color_hex": color,
                    "material": material,
                    "spool_id": spool_id,
                    "lot_nr_color": lot_nr_color,
                }
            except Exception as e:
                self.log(
                    f"3MF_SLOT_DATA: Error reading slot {slot}: {e}",
                    level="WARNING",
                )
        return slot_data

    # ── dedup persistence ─────────────────────────────────────────────

    def _load_seen_job_keys(self):
        try:
            with open(SEEN_JOBS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                keys = [str(k) for k in raw if k]
            elif isinstance(raw, dict):
                keys = [str(k) for k in raw if k]
            else:
                keys = []
            if len(keys) > MAX_SEEN_JOBS:
                keys = keys[-MAX_SEEN_JOBS:]
            return OrderedDict.fromkeys(keys, True)
        except FileNotFoundError:
            return OrderedDict()
        except (json.JSONDecodeError, TypeError, OSError) as e:
            self.log(
                f"AmsPrintUsageSync: could not load seen_job_keys from {SEEN_JOBS_PATH}: {e}. Starting empty.",
                level="WARNING",
            )
            return OrderedDict()

    def _ensure_data_dir(self):
        try:
            dir_path = os.path.dirname(SEEN_JOBS_PATH)
            os.makedirs(dir_path, exist_ok=True)
            if not os.path.isfile(SEEN_JOBS_PATH):
                with open(SEEN_JOBS_PATH, "w", encoding="utf-8") as f:
                    json.dump([], f)
        except OSError as e:
            self.log(
                f"AmsPrintUsageSync: could not ensure data dir {os.path.dirname(SEEN_JOBS_PATH)}: {e}",
                level="WARNING",
            )

    def _persist_seen_job_keys(self):
        try:
            dir_path = os.path.dirname(SEEN_JOBS_PATH)
            os.makedirs(dir_path, exist_ok=True)
            keys = list(self._seen_job_keys.keys())
            tmp_path = SEEN_JOBS_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(keys, f, indent=None)
            os.replace(tmp_path, SEEN_JOBS_PATH)
        except Exception as e:
            self.log(
                f"PERSIST_JOB_KEYS_FAILED: {e}",
                level="ERROR",
            )
            try:
                os.unlink(SEEN_JOBS_PATH + ".tmp")
            except OSError:
                pass

    # ── active print persistence ────────────────────────────────────

    def _persist_active_print(self, threemf_unavailable: bool = False) -> None:
        """Atomic write of active print state to disk."""
        if not self._job_key:
            return
        task_name = str(self.get_state(self._task_name_entity) or "")
        data = {
            "job_key": self._job_key,
            "task_name": task_name,
            "print_start_time": getattr(self, "_print_start_time", None),
            "start_snapshot": self._start_snapshot,
            "trays_used": sorted(self._trays_used) if self._trays_used else [],
            "spool_id_snapshot": self._spool_id_snapshot if hasattr(self, "_spool_id_snapshot") else {},
            "threemf_data": self._threemf_data,
            "threemf_file": self._threemf_filename,
            "threemf_fetched_at": (
                datetime.datetime.utcnow().isoformat() + "Z"
                if self._threemf_data else None
            ),
            "threemf_unavailable": threemf_unavailable,
        }
        try:
            tmp = ACTIVE_PRINT_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(ACTIVE_PRINT_FILE)
            self.log(
                f"ACTIVE_PRINT_PERSISTED job_key={self._job_key} "
                f"has_3mf={self._threemf_data is not None} "
                f"threemf_unavailable={threemf_unavailable}",
                level="INFO",
            )
        except Exception as e:
            self.log(f"ACTIVE_PRINT_PERSIST_FAILED: {e}", level="WARNING")

    def _load_active_print(self, job_key):
        """Read active_print.json on rehydrate. Returns dict with threemf_data, trays_used, spool_id_snapshot."""
        try:
            if not ACTIVE_PRINT_FILE.exists():
                return None
            data = json.loads(ACTIVE_PRINT_FILE.read_text())
            if data.get("job_key") != job_key:
                self.log(
                    f"ACTIVE_PRINT_STALE persisted={data.get('job_key')} "
                    f"current={job_key}",
                    level="INFO",
                )
                return None
            restored = {
                "threemf_data": data.get("threemf_data"),
                "trays_used": set(data.get("trays_used", [])),
                "spool_id_snapshot": data.get("spool_id_snapshot", {}),
            }
            self.log(
                f"ACTIVE_PRINT_RESTORED job_key={job_key} "
                f"has_3mf={restored['threemf_data'] is not None} "
                f"trays_used={len(restored['trays_used'])} "
                f"spool_ids={len(restored['spool_id_snapshot'])}",
                level="INFO",
            )
            return restored
        except Exception as e:
            self.log(f"ACTIVE_PRINT_LOAD_FAILED: {e}", level="WARNING")
            return None

    def _clear_active_print(self):
        """Delete active_print.json after print finish."""
        try:
            if ACTIVE_PRINT_FILE.exists():
                ACTIVE_PRINT_FILE.unlink()
                self.log("ACTIVE_PRINT_CLEARED", level="DEBUG")
        except Exception as e:
            self.log(f"ACTIVE_PRINT_CLEAR_FAILED: {e}", level="WARNING")

    # ── helpers ───────────────────────────────────────────────────────

    def _coerce_json_field(self, data, field):
        raw = data.get(field, {})
        if isinstance(raw, dict):
            return raw
        raw_str = str(raw).strip()
        if not raw_str:
            return {}
        try:
            parsed = json.loads(raw_str)
        except (json.JSONDecodeError, TypeError):
            self.log(
                f"USAGE_SKIP reason=JSON_PARSE_ERROR field={field} "
                f"raw={raw_str!r}",
                level="ERROR",
            )
            return None
        return parsed if isinstance(parsed, dict) else {}

    def _read_spool_id(self, slot):
        raw = self.get_state(f"input_text.ams_slot_{slot}_spool_id")
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    def _is_rfid_slot(self, slot):
        entity = self._tray_entity_by_slot.get(slot)
        if not entity:
            return False
        try:
            tag_uid = self.get_state(entity, attribute="tag_uid")
            val = str(tag_uid or "").strip()
            return val not in _INVALID_TAG_UIDS
        except Exception:
            return False

    def _is_tray_physically_present(self, slot):
        """Check if a spool is physically present in the tray via tag_uid or tray state."""
        entity = self._tray_entity_by_slot.get(slot)
        if not entity:
            return False
        try:
            tag_uid = str(self.get_state(entity, attribute="tag_uid") or "").strip()
            if tag_uid and tag_uid not in _INVALID_TAG_UIDS:
                return True
            tray_state = str(self.get_state(entity) or "").strip().lower()
            if tray_state and tray_state not in ("", "empty", "unknown", "unavailable"):
                return True
        except Exception:
            pass
        return False

    def _spoolman_get(self, path):
        url = f"{self.spoolman_base_url}{path}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    def _get_spool_display_name(self, spool_id, spools_cache=None):
        try:
            data = (spools_cache or {}).get(spool_id)
            if not data:
                data = self._spoolman_get(f"/api/v1/spool/{spool_id}")
            if data:
                f = data.get("filament", {})
                vendor = (
                    f.get("vendor", {}).get("name", "")
                    if f.get("vendor")
                    else ""
                )
                name = f.get("name", "")
                material = f.get("material", "")
                return f"{vendor} {name} {material}".strip()
        except Exception:
            pass
        return f"spool {spool_id}"

    def _get_spool_remaining(self, spool_id, spools_cache=None):
        try:
            data = (spools_cache or {}).get(spool_id)
            if not data:
                data = self._spoolman_get(f"/api/v1/spool/{spool_id}")
            if data:
                return float(data.get("remaining_weight", 0))
        except Exception:
            pass
        return 0.0

    def _spoolman_patch(self, spool_id, data):
        url = f"{self.spoolman_base_url}/api/v1/spool/{spool_id}"
        try:
            payload = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="PATCH")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            self.log(
                f"SPOOLMAN_PATCH_FAILED spool={spool_id}: {e}",
                level="ERROR",
            )
            return None

    def _reconcile_rfid_weights_deferred(self, kwargs):
        """Deferred reconcile callback — called via run_in after print finish."""
        if self._print_active:
            self.log(
                "RFID_WEIGHT_RECONCILE_DEFERRED_PRINT_ACTIVE "
                "— new print active, re-deferring 60s",
                level="INFO",
            )
            self.run_in(self._reconcile_rfid_weights_deferred, 60)
            return
        try:
            self._reconcile_rfid_weights()
        except Exception as exc:
            self.log(
                f"RFID_WEIGHT_RECONCILE_ERROR unhandled: {exc}",
                level="ERROR",
            )

    def _reconcile_rfid_weights(self):
        """After print finish, correct Spoolman remaining_weight using RFID ground truth."""
        if not self._weight_reconcile_enabled:
            return
        for slot in sorted(self._tray_entity_by_slot.keys()):
            try:
                self._reconcile_rfid_weight_slot(slot)
            except Exception as exc:
                self.log(
                    f"RFID_WEIGHT_RECONCILE_SLOT_ERROR slot={slot}: {exc}",
                    level="WARNING",
                )

    def _reconcile_rfid_weight_slot(self, slot):
        """Reconcile a single slot's RFID weight against Spoolman."""
        if not self._is_rfid_slot(slot):
            return
        spool_id = self._read_spool_id(slot)
        if spool_id <= 0:
            return
        entity = self._tray_entity_by_slot[slot]
        try:
            remain_raw = self.get_state(entity, attribute="remain")
            tray_weight = float(self.get_state(entity, attribute="tray_weight") or 0)
            remain_enabled = self.get_state(entity, attribute="remain_enabled")
        except (TypeError, ValueError):
            return
        if remain_enabled is False or str(remain_enabled).strip().lower() == "false":
            return
        if tray_weight <= 0:
            return
        if tray_weight < TRAY_WEIGHT_MIN_G or tray_weight > TRAY_WEIGHT_MAX_G:
            self.log(
                f"RFID_WEIGHT_SKIP_TRAY_WEIGHT slot={slot} "
                f"tray_weight={tray_weight} "
                f"reason=outside_sanity_bounds_{TRAY_WEIGHT_MIN_G}_{TRAY_WEIGHT_MAX_G}g",
                level="WARNING",
            )
            return
        # Validate remain value before calculation
        if remain_raw is None or not isinstance(remain_raw, (int, float)):
            try:
                remain_raw = float(remain_raw)
            except (TypeError, ValueError):
                self.log(
                    f"RFID_WEIGHT_INVALID_REMAIN slot={slot} remain={remain_raw!r}",
                    level="WARNING",
                )
                return
        remain = float(remain_raw)
        if remain < 0 or remain > 100:
            self.log(
                f"RFID_WEIGHT_INVALID_REMAIN slot={slot} remain={remain}",
                level="WARNING",
            )
            return
        rfid_weight_g = round(remain / 100.0 * tray_weight, 1)
        spool_data = self._spoolman_get(f"/api/v1/spool/{spool_id}")
        if spool_data is None:
            self.log(
                f"RFID_WEIGHT_RECONCILE_SKIP slot={slot} spool_id={spool_id} "
                f"reason=spoolman_fetch_failed",
                level="WARNING",
            )
            return
        spoolman_weight_g = round(float(spool_data.get("remaining_weight", 0)), 1)
        delta = abs(rfid_weight_g - spoolman_weight_g)
        if delta < RECONCILE_MIN_DELTA_G:
            self.log(
                f"RFID_WEIGHT_MATCH slot={slot} spool_id={spool_id} "
                f"rfid={rfid_weight_g}g delta={delta:.1f}g < "
                f"{RECONCILE_MIN_DELTA_G}g threshold",
                level="DEBUG",
            )
            return
        if self.dry_run:
            self.log(
                f"RFID_WEIGHT_RECONCILE_DRYRUN slot={slot} spool_id={spool_id} "
                f"rfid={rfid_weight_g}g spoolman_was={spoolman_weight_g}g",
                level="INFO",
            )
            return
        result = self._spoolman_patch(spool_id, {"remaining_weight": rfid_weight_g})
        if result is None:
            self.log(
                f"RFID_WEIGHT_RECONCILE_FAILED slot={slot} spool_id={spool_id} "
                f"rfid={rfid_weight_g}g",
                level="WARNING",
            )
        else:
            self.log(
                f"RFID_WEIGHT_RECONCILED slot={slot} spool_id={spool_id} "
                f"rfid={rfid_weight_g}g spoolman_was={spoolman_weight_g}g",
                level="INFO",
            )

    def _spoolman_use(self, spool_id, use_weight_g):
        """PUT /api/v1/spool/{id}/use — returns updated spool dict or None on failure."""
        url = f"{self.spoolman_base_url}/api/v1/spool/{spool_id}/use"
        payload = json.dumps(
            {"use_weight": round(use_weight_g, 2)}
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            method="PUT",
            headers={"Content-Type": "application/json"},
            data=payload,
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode())
                return None
        except Exception as exc:
            self.log(
                f"USAGE_PATCH_FAILED spool_id={spool_id} "
                f"use_weight={use_weight_g:.1f} error={exc}",
                level="ERROR",
            )
            return None
