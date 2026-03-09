"""AMS Print Usage Sync — writes filament consumption to Spoolman after each print.

Entity naming: sensor.{prefix}_{sensor_name} where prefix = _build_entity_prefix().
See base.py for the pattern (printer_model + printer_serial lowercased).

Triggered by custom HA event P1S_PRINT_USAGE_READY fired by the
p1s_remaining_snapshot_on_finish automation.

RFID slots:     consumption = start_g - end_g from fuel gauge snapshots.
Non-RFID slots: time-weighted by tray active duration, or equal split fallback.

Tray tracking:  AppDaemon listens to tray active attribute; replaces HA automation
p1s_record_trays_used_during_print (avoids mode:restart race conditions).

Slot-to-spool mapping: input_text.ams_slot_{slot}_spool_id (reconciler-owned, read-only).
Spoolman write:         PUT /api/v1/spool/{id}/use {"use_weight": grams}
Dedup:                  job_key set persisted to disk (capped at 50 entries).
"""

import datetime
import json
import os
import tempfile
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

# tag_uid values that indicate non-RFID (no chip or empty)
_INVALID_TAG_UIDS = frozenset({"", "0000000000000000", "unknown", "unavailable"})


class AmsPrintUsageSync(FilamentIQBase):

    def initialize(self):
        # Validate required config first
        self._validate_config(["spoolman_url", "printer_serial"])

        self.enabled = bool(self.args.get("enabled", True))
        self.spoolman_base_url = str(
            self.args.get("spoolman_url", self.args.get("spoolman_base_url", ""))
        ).rstrip("/")
        self.dry_run = bool(self.args.get("dry_run", False))
        self.min_consumption_g = float(self.args.get("min_consumption_g", 2))
        self.max_consumption_g = float(self.args.get("max_consumption_g", 300))
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
        self._tray_active_times = {}
        self._current_active_slot = None
        self._print_active = False

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

        # Phase 3: Debug logging, swap detection, rehydrate (absorbs automations E, F, G)
        self._lifecycle_phase3 = bool(self.args.get("lifecycle_phase3_enabled", False))
        self._last_swap_warn_time = None
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

        self.listen_event(self._handle_usage_event, "P1S_PRINT_USAGE_READY")

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

    # ── event handler ────────────────────────────────────────────────

    def _handle_usage_event(self, event_name, data, kwargs):
        job_key = str(data.get("job_key", "")).strip()
        task_name = str(data.get("task_name", "")).strip()
        print_status = str(data.get("print_status", "")).strip().lower()

        # ── Fix 1: skip failed/cancelled prints ──
        if print_status in self._FAILED_STATES:
            self.log(
                f"USAGE_SKIP_FAILED_PRINT job_key={job_key} status={print_status}",
                level="INFO",
            )
            return

        try:
            print_weight_g = float(data.get("print_weight_g", 0))
        except (TypeError, ValueError):
            print_weight_g = 0.0

        if self._trays_used:
            trays_used_set = set(self._trays_used)
        else:
            trays_used_raw = str(data.get("trays_used", "")).strip()
            trays_used_set = set()
            max_slot = self._get_max_slot()
            if trays_used_raw:
                for part in trays_used_raw.replace(" ", "").split(","):
                    try:
                        slot_int = int(part)
                        if 1 <= slot_int <= max_slot:
                            trays_used_set.add(slot_int)
                    except (TypeError, ValueError):
                        pass
            if trays_used_set:
                self.log(
                    f"TRAY_TRACKING_FALLBACK using event data trays_used={trays_used_set}",
                    level="WARNING",
                )

        if job_key and job_key in self._seen_job_keys:
            self.log(f"DEDUP_SKIP job_key={job_key}", level="INFO")
            return

        start_map = self._coerce_json_field(data, "start_json")
        end_map = self._coerce_json_field(data, "end_json")
        if start_map is None or end_map is None:
            return

        if not start_map:
            self.log(
                f"USAGE_SKIP reason=NO_START_SNAPSHOT job_key={job_key}",
                level="INFO",
            )
            return

        if trays_used_set:
            active_slots = sorted(trays_used_set)
        else:
            max_slot = self._get_max_slot()
            active_slots = sorted(
                int(k) for k in start_map
                if k.isdigit() and 1 <= int(k) <= max_slot
            )
            if active_slots:
                self.log(
                    f"USAGE_NO_TRAY_TRACKING: using start_map keys as "
                    f"active_slots={active_slots}",
                    level="WARNING",
                )

        threemf_matched_slots = {}
        threemf_used = False
        threemf_has_unmatched = False
        all_results = []
        skipped = 0

        if self._threemf_data and self.threemf_enabled:
            slot_data = self._build_slot_data()
            matches, unmatched_fils = match_filaments_to_slots(
                self._threemf_data, slot_data, trays_used=None
            )
            if matches:
                threemf_used = True
                for m in matches:
                    threemf_matched_slots[m["slot"]] = m["used_g"]
                    self.log(
                        f"3MF_MATCH slot={m['slot']} spool_id={m['spool_id']} "
                        f"used_g={m['used_g']:.2f} method={m['method']}",
                        level="INFO",
                    )
                if unmatched_fils:
                    threemf_has_unmatched = True
                    unmatched_total = sum(f["used_g"] for f in unmatched_fils)
                    self.log(
                        f"3MF_UNMATCHED filaments="
                        f"{[(f['index'], f['used_g'], f['color_hex']) for f in unmatched_fils]} "
                        f"unmatched_total_g={unmatched_total:.2f} "
                        f"(will flow to time_weighted/equal_split pool)",
                        level="WARNING",
                    )
            else:
                self.log(
                    "3MF_MATCH: No matches found — falling back to estimation",
                    level="WARNING",
                )

        if threemf_matched_slots:
            active_slots = sorted(set(active_slots) | set(threemf_matched_slots.keys()))

        rfid_total_g = 0.0
        remaining_nonrfid_slots = []

        for slot in active_slots:
            spool_id = self._read_spool_id(slot)
            if spool_id <= 0:
                self.log(
                    f"USAGE_SKIP slot={slot} reason=UNBOUND", level="INFO"
                )
                skipped += 1
                continue

            if slot in threemf_matched_slots:
                consumption_g = threemf_matched_slots[slot]
                all_results.append((slot, spool_id, consumption_g, "3mf"))
                self.log(
                    f"USAGE_3MF slot={slot} spool_id={spool_id} "
                    f"consumption_g={consumption_g:.2f}",
                    level="INFO",
                )
                continue

            is_rfid = self._is_rfid_slot(slot)
            start_g = float(start_map.get(str(slot), 0))
            end_g = float(end_map.get(str(slot), 0))

            # When 3MF had unmatched filaments, route unmatched slots to
            # pool-based estimation instead of rfid_delta (which may be 0g
            # due to fuel gauge inaccuracy). The unmatched 3MF consumption
            # is already in the pool (print_weight - matched_3mf).
            if threemf_has_unmatched:
                remaining_nonrfid_slots.append((slot, spool_id))
                self.log(
                    f"USAGE_3MF_UNMATCHED_POOL slot={slot} spool_id={spool_id} "
                    f"is_rfid={is_rfid} (routed to pool due to unmatched 3MF filaments)",
                    level="INFO",
                )
                continue

            if is_rfid and start_g > 0 and end_g > 0:
                consumption_g = max(0.0, start_g - end_g)
                rfid_total_g += consumption_g
                all_results.append((slot, spool_id, consumption_g, "rfid_delta"))
                self.log(
                    f"USAGE_RFID slot={slot} spool_id={spool_id} "
                    f"consumption_g={consumption_g:.1f}",
                    level="INFO",
                )
                continue

            remaining_nonrfid_slots.append((slot, spool_id))
            self.log(
                f"USAGE_NONRFID_SLOT slot={slot} spool_id={spool_id} "
                f"is_rfid={is_rfid} start_g={start_g:.1f} end_g={end_g:.1f}",
                level="INFO",
            )

        if rfid_total_g > print_weight_g and print_weight_g > 0:
            self.log(
                f"USAGE_RFID_CAP rfid_total={rfid_total_g:.1f} > "
                f"print_weight={print_weight_g:.1f} — capping to print_weight",
                level="WARNING",
            )
            rfid_total_g = print_weight_g

        if remaining_nonrfid_slots:
            threemf_total = sum(
                c for s, _, c, m in all_results if m == "3mf"
            )

            # ── Fix 2: zero-delta guard ──
            # Check actual fuel gauge deltas across ALL slots (not just RFID totals).
            # If every slot shows start == end AND no 3MF data, there's no evidence
            # filament was consumed — don't trust slicer estimate as pool.
            all_deltas_zero = all(
                float(start_map.get(str(s), 0)) == float(end_map.get(str(s), 0))
                for s in active_slots
            )
            if all_deltas_zero and threemf_total == 0 and rfid_total_g == 0:
                pool_g = 0.0
                self.log(
                    f"USAGE_POOL_ZEROED reason=no_consumption_evidence "
                    f"print_weight_g={print_weight_g:.1f}",
                    level="WARNING",
                )
            else:
                pool_g = max(
                    0.0, print_weight_g - threemf_total - rfid_total_g
                )

            if pool_g <= 0 and (threemf_total + rfid_total_g) > print_weight_g:
                self.log(
                    f"USAGE_POOL_EXHAUSTED 3mf+rfid={threemf_total + rfid_total_g:.1f} "
                    f"> print_weight={print_weight_g:.1f} — non-RFID pool is 0",
                    level="WARNING",
                )

            time_weights = self._get_time_weights()
            nonrfid_slot_ids = {s for s, _ in remaining_nonrfid_slots}
            relevant_weights = {
                s: w for s, w in time_weights.items() if s in nonrfid_slot_ids
            }

            if relevant_weights and sum(relevant_weights.values()) > 0:
                weight_total = sum(relevant_weights.values())
                method = "time_weighted"
            else:
                relevant_weights = {s: 1.0 for s, _ in remaining_nonrfid_slots}
                weight_total = len(remaining_nonrfid_slots)
                method = "equal_split"

            for slot, spool_id in remaining_nonrfid_slots:
                w = relevant_weights.get(slot, 0)
                consumption_g = (
                    (pool_g * w / weight_total) if weight_total > 0 else 0.0
                )
                all_results.append((slot, spool_id, consumption_g, method))
                self.log(
                    f"USAGE_NONRFID slot={slot} spool_id={spool_id} "
                    f"consumption_g={consumption_g:.2f} pool_g={pool_g:.1f} "
                    f"method={method} weight={w:.4f}",
                    level="INFO",
                )

        patched = 0

        for slot, spool_id, consumption_g, method in all_results:
            if consumption_g > self.max_consumption_g:
                self.log(
                    f"USAGE_SANITY_CAP slot={slot} consumption_g={consumption_g:.1f} "
                    f"> max={self.max_consumption_g} — SKIPPING",
                    level="ERROR",
                )
                skipped += 1
                continue

            if consumption_g < self.min_consumption_g:
                self.log(
                    f"USAGE_BELOW_MIN slot={slot} consumption_g={consumption_g:.2f} "
                    f"< min={self.min_consumption_g} — skipping",
                    level="INFO",
                )
                skipped += 1
                continue

            if self.dry_run:
                self.log(
                    f"WOULD_PATCH slot={slot} spool_id={spool_id} "
                    f"use_weight={consumption_g:.1f} method={method} "
                    f"job_key={job_key}",
                    level="INFO",
                )
                patched += 1
                continue

            ok = self._spoolman_use(spool_id, consumption_g)
            if ok:
                self.log(
                    f"USAGE_PATCHED slot={slot} spool_id={spool_id} "
                    f"consumption_g={consumption_g:.2f} method={method} "
                    f"job_key={job_key}",
                    level="INFO",
                )
                patched += 1
                try:
                    spool_data = self._spoolman_get(f"/api/v1/spool/{spool_id}")
                    if spool_data and float(
                        spool_data.get("remaining_weight", 1)
                    ) <= 0:
                        self._spoolman_patch(spool_id, {"location": "Empty"})
                        self.call_service(
                            "input_text/set_value",
                            entity_id=f"input_text.ams_slot_{slot}_spool_id",
                            value="0",
                        )
                        self.call_service(
                            "input_text/set_value",
                            entity_id=f"input_text.ams_slot_{slot}_unbound_reason",
                            value="UNBOUND_TRAY_EMPTY",
                        )
                        self.log(
                            f"USAGE_SPOOL_DEPLETED slot={slot} spool_id={spool_id} "
                            f"— moved to Empty",
                            level="WARNING",
                        )
                except Exception as e:
                    self.log(
                        f"USAGE_DEPLETED_CHECK_FAILED: {e}",
                        level="WARNING",
                    )
            else:
                skipped += 1

        if job_key:
            self._seen_job_keys[job_key] = True
            while len(self._seen_job_keys) > MAX_SEEN_JOBS:
                self._seen_job_keys.popitem(last=False)
            self._persist_seen_job_keys()

        total_consumed = sum(c for _, _, c, _ in all_results)
        threemf_count = sum(1 for _, _, _, m in all_results if m == "3mf")
        rfid_count = sum(1 for _, _, _, m in all_results if m == "rfid_delta")
        nonrfid_count = sum(
            1 for _, _, _, m in all_results
            if m in ("time_weighted", "equal_split")
        )
        self.log(
            f"USAGE_SUMMARY job_key={job_key} task={task_name} "
            f"status={print_status} "
            f"3mf_slots={threemf_count} rfid_slots={rfid_count} "
            f"nonrfid_slots={nonrfid_count} "
            f"trays_used={trays_used_set or 'all'} "
            f"tray_times={self._summarize_tray_times()} "
            f"threemf_file={self._threemf_filename or 'none'} "
            f"total_consumed_g={total_consumed:.1f} "
            f"patched={patched} skipped={skipped}",
            level="INFO",
        )

        job_label = task_name.replace(".gcode.3mf", "").replace(".3mf", "").strip()
        lines = [f"Job: {job_label}", f"Status: {print_status}", ""]
        for slot, spool_id, consumption_g, method in all_results:
            spool_name = self._get_spool_display_name(spool_id)
            remaining = self._get_spool_remaining(spool_id)
            in_range = (
                consumption_g >= self.min_consumption_g
                and consumption_g <= self.max_consumption_g
            )
            was_written = in_range and not self.dry_run
            if was_written:
                lines.append(
                    f"Slot {slot}: {spool_name} — used {consumption_g:.1f}g "
                    f"({remaining:.0f}g left) [{method}]"
                )
            elif in_range and self.dry_run:
                lines.append(
                    f"Slot {slot}: {spool_name} — used {consumption_g:.1f}g "
                    f"({remaining:.0f}g left) [dry run, not written] [{method}]"
                )
            elif consumption_g > self.max_consumption_g:
                lines.append(
                    f"Slot {slot}: {spool_name} — {consumption_g:.1f}g "
                    f"SKIPPED (exceeds {self.max_consumption_g:.0f}g cap) [{method}]"
                )
            elif consumption_g < self.min_consumption_g:
                lines.append(
                    f"Slot {slot}: {spool_name} — {consumption_g:.1f}g "
                    f"(below {self.min_consumption_g:.0f}g min, not written) [{method}]"
                )
        if not all_results:
            lines.append("No filament consumption recorded.")
            if not trays_used_set:
                lines.append("(No tray activity detected)")
            if not self._threemf_data:
                lines.append("(3MF data unavailable)")
        lines.append("")
        lines.append(f"Total: {total_consumed:.1f}g | Slicer estimate: {print_weight_g:.1f}g")
        notification_msg = "\n".join(lines)
        notify_target = self.args.get("notify_target")
        try:
            if notify_target:
                self.call_service(
                    "notify/notify",
                    target=notify_target,
                    title=f"P1S Filament Usage ({print_status})",
                    message=notification_msg,
                )
            else:
                self.call_service(
                    "notify/persistent_notification",
                    title=f"P1S Filament Usage ({print_status})",
                    message=notification_msg,
                )
        except Exception as e:
            self.log(f"USAGE_NOTIFY_FAILED: {e}", level="WARNING")

        self._threemf_data = None
        self._threemf_filename = None

    # ── tray activity tracking ────────────────────────────────────────

    _PAUSE_STATES = frozenset({"pause", "paused"})

    def _on_print_status_change(self, entity, attribute, old, new, kwargs):
        # Debug logging (replaces automation E)
        self.log(f"PRINT_STATUS_TRANSITION from={old} to={new}", level="DEBUG")

        if new in ("running", "printing") and old not in ("running", "printing"):
            self._trays_used = set()
            self._tray_active_times = {}
            self._current_active_slot = None
            self._print_active = True
            self._seed_active_trays()
            self.log(
                f"TRAY_TRACKING_START trays_used={self._trays_used}",
                level="INFO",
            )
            if self.threemf_enabled:
                self.run_in(self._fetch_3mf_background, 5)
            if self._lifecycle_phase1:
                self._on_print_start()
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
            fg = float(self.get_state(fg_entity) or -1)
        except (TypeError, ValueError):
            fg = -1.0
        if fg > 0:
            return fg
        ams_entity = self._ams_remaining_pattern.format(slot=slot)
        try:
            ams = float(self.get_state(ams_entity) or -1)
        except (TypeError, ValueError):
            ams = -1.0
        return ams if ams > 0 else -1.0

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
        task_name = str(self.get_state(self._task_name_entity) or "")
        self._job_key = task_name.replace(" ", "_")
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

        self.log(
            f"PRINT_START_CAPTURED job_key={self._job_key} "
            f"start_snapshot={self._start_snapshot}",
            level="INFO",
        )

    def _seed_slot_start_grams(self, slot):
        """Write-once: seed start grams for a newly-active slot during print."""
        if slot in self._start_snapshot and self._start_snapshot[slot] > 0:
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

    _TERMINAL_STATES = frozenset({"finish", "finished", "completed", "failed", "error"})
    _FAILED_STATES = frozenset({"failed", "error", "canceled"})

    def _build_end_snapshot(self):
        """Read fuel gauge for slots present in start_snapshot. Returns {slot_int: grams}."""
        snapshot = {}
        for slot in sorted(self._start_snapshot.keys()):
            grams = self._read_fuel_gauge(slot)
            if grams >= 0:
                snapshot[slot] = max(0.0, round(grams, 1))
        return snapshot

    def _on_print_finish(self, new_status):
        """Phase 2 print finish handler: build end snapshot, call usage handler, clean up."""
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

        # Build end snapshot
        self._end_snapshot = self._build_end_snapshot()

        # Read print weight
        try:
            print_weight_g = float(self.get_state(self._print_weight_entity) or 0)
        except (TypeError, ValueError):
            print_weight_g = 0.0

        task_name = str(self.get_state(self._task_name_entity) or "")

        # Build data dict matching _handle_usage_event expectations
        data = {
            "job_key": self._job_key,
            "task_name": task_name,
            "print_weight_g": print_weight_g,
            "trays_used": ",".join(str(s) for s in sorted(self._trays_used)),
            "start_json": self._snapshot_to_json_dict(self._start_snapshot),
            "end_json": self._snapshot_to_json_dict(self._end_snapshot),
            "print_status": status,
        }

        self.log(
            f"PRINT_FINISH_CAPTURED job_key={self._job_key} "
            f"end_snapshot={self._end_snapshot} status={status}",
            level="INFO",
        )

        # Call usage handler directly (no event roundtrip)
        self._handle_usage_event(None, data, {})

        # Stamp dedup — only for non-failed prints so a retry with the
        # same job_key after a failure is not incorrectly skipped
        if status not in self._FAILED_STATES:
            self._last_processed_job_key = self._job_key

        # Clean up (includes setting print_active off)
        self._end_snapshot = {}
        self._on_print_end()

    # ── Phase 3: debug logging, swap detection, rehydrate ──────────

    def _on_spool_id_change(self, entity, attribute, old, new, kwargs):
        """Detect spool mapping changes during active print (replaces automation F)."""
        if not self._lifecycle_phase3:
            return
        if not self._print_active:
            return
        # Startup suppression
        if self._startup_suppress_until and datetime.datetime.utcnow() < self._startup_suppress_until:
            return
        # Cooldown (5 minutes between warnings)
        now = datetime.datetime.utcnow()
        if self._last_swap_warn_time:
            elapsed = (now - self._last_swap_warn_time).total_seconds()
            if elapsed < 300:
                return
        self._last_swap_warn_time = now
        self.log(
            f"SPOOL_SWAP_DURING_PRINT entity={entity} old={old} new={new}",
            level="WARNING",
        )
        try:
            self.call_service(
                "input_boolean/turn_on",
                entity_id=self._needs_reconcile_entity,
            )
        except Exception as e:
            self.log(f"SWAP_DETECT: Failed to set needs_reconcile: {e}", level="WARNING")
        try:
            self.call_service(
                "notify/persistent_notification",
                title="P1S Spool Swap During Print Detected",
                message=f"{entity} changed from {old} to {new} while print active. "
                        f"Manual reconciliation may be required.",
            )
        except Exception as e:
            self.log(f"SWAP_DETECT: Failed to notify: {e}", level="WARNING")

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
            task_name = str(self.get_state(self._task_name_entity) or "")
            self._job_key = task_name.replace(" ", "_")
            try:
                self.call_service(
                    "input_text/set_value",
                    entity_id=self._job_key_entity,
                    value=self._job_key,
                )
            except Exception:
                pass

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

    def _get_time_weights(self):
        times = self._summarize_tray_times()
        total = sum(times.values())
        if total <= 0:
            return {}
        return {slot: round(t / total, 4) for slot, t in times.items()}

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
        max_attempts = 3
        retry_delays = [10, 30]

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
                return

            best_file = find_best_3mf(file_list, task_name)
            if not best_file:
                self.log(
                    f"3MF_FETCH: No match for task={task_name} in {file_list}",
                    level="WARNING",
                )
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
                    return

                filaments = parse_3mf_filaments(local_path)
                if not filaments:
                    self.log(
                        f"3MF_FETCH: No filament data in {best_file}",
                        level="WARNING",
                    )
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
        max_attempts = 3
        retry_delays = [10, 30]

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
            return

        best_file = find_best_3mf(file_list, task_name)
        if not best_file:
            self.log(
                f"3MF_FETCH: No match for task={task_name} in {file_list}",
                level="WARNING",
            )
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
                return

            filaments = parse_3mf_filaments(local_path)
            if not filaments:
                self.log(
                    f"3MF_FETCH: No filament data in {best_file}",
                    level="WARNING",
                )
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

    def _build_slot_data(self):
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
                        spool_data = self._spoolman_get(f"/api/v1/spool/{spool_id}")
                        if spool_data:
                            lot_nr = str(spool_data.get("lot_nr", "") or "")
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
            with open(SEEN_JOBS_PATH, "w", encoding="utf-8") as f:
                json.dump(keys, f, indent=None)
        except OSError as e:
            self.log(
                f"AmsPrintUsageSync: could not persist seen_job_keys to {SEEN_JOBS_PATH}: {e}",
                level="WARNING",
            )

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

    def _spoolman_get(self, path):
        url = f"{self.spoolman_base_url}{path}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    def _get_spool_display_name(self, spool_id):
        try:
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

    def _get_spool_remaining(self, spool_id):
        try:
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

    def _spoolman_use(self, spool_id, use_weight_g):
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
                return resp.status == 200
        except Exception as exc:
            self.log(
                f"USAGE_PATCH_FAILED spool_id={spool_id} "
                f"use_weight={use_weight_g:.1f} error={exc}",
                level="ERROR",
            )
            return False
