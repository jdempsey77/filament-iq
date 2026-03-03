"""AMS Print Usage Sync — writes filament consumption to Spoolman after each print.

Triggered by custom HA event P1S_PRINT_USAGE_READY fired by the
p1s_remaining_snapshot_on_finish automation.

RFID slots:     consumption = start_g - end_g from fuel gauge snapshots.
Non-RFID slots: time-weighted by tray active duration, or equal split fallback.

Tray tracking:  AppDaemon listens to tray active attribute; replaces HA automation
p1s_record_trays_used_during_print (avoids mode:restart race conditions).

Slot-to-spool mapping: input_text.ams_slot_{1-6}_spool_id (reconciler-owned, read-only).
Spoolman write:         PUT /api/v1/spool/{id}/use {"use_weight": grams}
Dedup:                  job_key set persisted to disk (capped at 50 entries).
"""

import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

import hassapi as hass

# Path next to this app so it works under /config/appdaemon/apps or /addon_configs/.../apps
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_JOBS_PATH = os.path.join(_APP_DIR, "data", "seen_job_keys.json")

TRAY_ENTITY_BY_SLOT = {
    1: "sensor.p1s_01p00c5a3101668_ams_1_tray_1",
    2: "sensor.p1s_01p00c5a3101668_ams_1_tray_2",
    3: "sensor.p1s_01p00c5a3101668_ams_1_tray_3",
    4: "sensor.p1s_01p00c5a3101668_ams_1_tray_4",
    5: "sensor.p1s_01p00c5a3101668_ams_128_tray_1",
    6: "sensor.p1s_01p00c5a3101668_ams_129_tray_1",
}
SLOT_BY_TRAY_ENTITY = {v: k for k, v in TRAY_ENTITY_BY_SLOT.items()}

MAX_SEEN_JOBS = 50


class AmsPrintUsageSync(hass.Hass):

    def initialize(self):
        self.enabled = bool(self.args.get("enabled", True))
        self.spoolman_base_url = str(
            self.args.get("spoolman_base_url", "")
        ).rstrip("/")
        self.dry_run = bool(self.args.get("dry_run", False))
        self.min_consumption_g = float(self.args.get("min_consumption_g", 2))
        self.max_consumption_g = float(self.args.get("max_consumption_g", 300))
        self._seen_job_keys = self._load_seen_job_keys()
        self._ensure_data_dir()

        # Tray activity tracking (replaces HA automation p1s_record_trays_used_during_print)
        self._trays_used = set()
        self._tray_active_times = {}  # {slot: [{'start': timestamp, 'end': timestamp}, ...]}
        self._current_active_slot = None
        self._print_active = False

        if not self.enabled:
            self.log("AmsPrintUsageSync disabled via config", level="WARNING")
            return

        self.listen_event(self._handle_usage_event, "P1S_PRINT_USAGE_READY")

        # Listen for tray active attribute changes
        for tray_entity in SLOT_BY_TRAY_ENTITY:
            self.listen_state(
                self._on_tray_active_change,
                tray_entity,
                attribute="active",
            )

        # Listen for print status to know when to start/stop tracking
        self.listen_state(
            self._on_print_status_change,
            "sensor.p1s_01p00c5a3101668_print_status",
        )

        self.log(
            f"AmsPrintUsageSync initialized  dry_run={self.dry_run}  "
            f"min_consumption_g={self.min_consumption_g}  "
            f"spoolman={self.spoolman_base_url}",
            level="INFO",
        )

    # ── event handler ────────────────────────────────────────────────

    def _handle_usage_event(self, event_name, data, kwargs):
        job_key = str(data.get("job_key", "")).strip()
        task_name = str(data.get("task_name", "")).strip()
        print_status = str(data.get("print_status", "")).strip().lower()

        try:
            print_weight_g = float(data.get("print_weight_g", 0))
        except (TypeError, ValueError):
            print_weight_g = 0.0

        # Use internally-tracked trays_used (from tray active listeners)
        # Fall back to event data if internal tracking is empty (e.g. AppDaemon restarted mid-print)
        if self._trays_used:
            trays_used_set = set(self._trays_used)
        else:
            trays_used_raw = str(data.get("trays_used", "")).strip()
            trays_used_set = set()
            if trays_used_raw:
                for part in trays_used_raw.replace(" ", "").split(","):
                    try:
                        slot_int = int(part)
                        if 1 <= slot_int <= 6:
                            trays_used_set.add(slot_int)
                    except (TypeError, ValueError):
                        pass
            if trays_used_set:
                self.log(
                    f"TRAY_TRACKING_FALLBACK using event data trays_used={trays_used_set}",
                    level="WARNING",
                )

        # ── dedup ────────────────────────────────────────────────────
        if job_key and job_key in self._seen_job_keys:
            self.log(f"DEDUP_SKIP job_key={job_key}", level="INFO")
            return

        # ── parse JSON payloads (HA native types may pass dicts) ─────
        start_map = self._coerce_json_field(data, "start_json")
        end_map = self._coerce_json_field(data, "end_json")
        if start_map is None or end_map is None:
            return

        # ── guard: no start data = cancelled before print ────────────
        if not start_map:
            self.log(
                f"USAGE_SKIP reason=NO_START_SNAPSHOT job_key={job_key}",
                level="INFO",
            )
            return

        # ── build slot list ──────────────────────────────────────────
        active_slots = sorted(int(k) for k in start_map if k.isdigit() and 1 <= int(k) <= 6)

        # ── compute per-slot consumption ─────────────────────────────
        rfid_results = []
        nonrfid_slots = []
        skipped = 0

        for slot in active_slots:
            spool_id = self._read_spool_id(slot)
            if spool_id <= 0:
                self.log(
                    f"USAGE_SKIP slot={slot} reason=UNBOUND", level="INFO"
                )
                skipped += 1
                continue

            start_g = float(start_map.get(str(slot), 0))
            end_g = float(end_map.get(str(slot), 0))

            # A slot is RFID-trackable only if BOTH start and end have
            # positive readings (fuel gauge was available for both snapshots).
            # If end_g is 0 or missing, the slot has no fuel gauge — treat
            # as non-RFID for estimation.
            has_fuel_gauge = start_g > 0 and end_g > 0

            if has_fuel_gauge:
                consumption_g = max(0.0, start_g - end_g)
                rfid_results.append((slot, spool_id, consumption_g))
            else:
                # Only charge non-RFID slots that were actually used during this print
                if trays_used_set and slot not in trays_used_set:
                    self.log(
                        f"USAGE_NONRFID_SKIP_NOT_USED slot={slot} spool_id={spool_id} "
                        f"trays_used={trays_used_set}",
                        level="INFO",
                    )
                    skipped += 1
                    continue
                nonrfid_slots.append((slot, spool_id))

        # ── non-RFID allocation ──────────────────────────────────────
        rfid_total_g = sum(c for _, _, c in rfid_results)
        # Cap RFID total to print weight — fuel gauge has coarse resolution
        # and can over-report consumption for small prints
        if rfid_total_g > print_weight_g and print_weight_g > 0:
            self.log(
                f"USAGE_RFID_CAP rfid_total={rfid_total_g:.1f} > "
                f"print_weight={print_weight_g:.1f} — capping to print_weight",
                level="WARNING",
            )
            rfid_total_g = print_weight_g
        nonrfid_pool_g = max(0.0, print_weight_g - rfid_total_g)

        # Try time-weighted allocation, fall back to equal split
        time_weights = self._get_time_weights()
        nonrfid_slot_ids = set(slot for slot, _ in nonrfid_slots)
        relevant_weights = {
            s: w for s, w in time_weights.items() if s in nonrfid_slot_ids
        }

        if relevant_weights and sum(relevant_weights.values()) > 0:
            weight_total = sum(relevant_weights.values())
            allocation_method = "time_weighted"
        else:
            relevant_weights = {s: 1.0 for s, _ in nonrfid_slots}
            weight_total = len(nonrfid_slots) if nonrfid_slots else 1.0
            allocation_method = "equal_split"

        nonrfid_results = []
        for slot, spool_id in nonrfid_slots:
            slot_weight = relevant_weights.get(slot, 0)
            slot_share_g = (
                (nonrfid_pool_g * slot_weight / weight_total)
                if weight_total > 0
                else 0.0
            )
            self.log(
                f"USAGE_NONRFID_ESTIMATE slot={slot} spool_id={spool_id} "
                f"estimated_g={slot_share_g:.1f} pool_g={nonrfid_pool_g:.1f} "
                f"method={allocation_method} weight={slot_weight:.4f} "
                f"slots={len(nonrfid_slots)}",
                level="INFO",
            )
            nonrfid_results.append((slot, spool_id, slot_share_g))

        # ── write to Spoolman ────────────────────────────────────────
        patched = 0
        all_results = rfid_results + nonrfid_results

        for slot, spool_id, consumption_g in all_results:
            method = (
                "rfid_delta"
                if any(s == slot for s, _, _ in rfid_results)
                else "nonrfid_estimate"
            )

            # Sanity cap: refuse to log unreasonably large consumption
            if consumption_g > self.max_consumption_g:
                self.log(
                    f"USAGE_SANITY_CAP slot={slot} spool_id={spool_id} "
                    f"consumption_g={consumption_g:.1f} "
                    f"max={self.max_consumption_g} method={method} "
                    f"— REFUSING TO WRITE",
                    level="ERROR",
                )
                skipped += 1
                continue

            if consumption_g < self.min_consumption_g:
                self.log(
                    f"USAGE_BELOW_MIN slot={slot} spool_id={spool_id} "
                    f"consumption_g={consumption_g:.1f} "
                    f"min={self.min_consumption_g}",
                    level="DEBUG",
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
                    f"use_weight={consumption_g:.1f} method={method} "
                    f"job_key={job_key}",
                    level="INFO",
                )
                patched += 1
            else:
                skipped += 1

        # ── record job_key ───────────────────────────────────────────
        if job_key:
            self._seen_job_keys[job_key] = True
            while len(self._seen_job_keys) > MAX_SEEN_JOBS:
                self._seen_job_keys.popitem(last=False)
            self._persist_seen_job_keys()

        # ── summary ──────────────────────────────────────────────────
        total_consumed = sum(c for _, _, c in all_results)
        self.log(
            f"USAGE_SUMMARY job_key={job_key} task={task_name} "
            f"status={print_status} "
            f"rfid_slots={len(rfid_results)} "
            f"nonrfid_slots={len(nonrfid_results)} "
            f"trays_used={trays_used_set or 'all'} "
            f"tray_times={self._summarize_tray_times()} "
            f"allocation={allocation_method} "
            f"total_consumed_g={total_consumed:.1f} "
            f"patched={patched} skipped={skipped}",
            level="INFO",
        )

    # ── tray activity tracking ────────────────────────────────────────

    def _on_print_status_change(self, entity, attribute, old, new, kwargs):
        """Track print start/stop for tray activity recording."""
        if new in ("running", "printing") and old not in ("running", "printing"):
            # Print started — clear tracking
            self._trays_used = set()
            self._tray_active_times = {}
            self._current_active_slot = None
            self._print_active = True
            self._seed_active_trays()
            self.log(
                f"TRAY_TRACKING_START trays_used={self._trays_used}",
                level="INFO",
            )
        elif old in ("running", "printing") and new not in ("running", "printing"):
            # Print ended — close any open time segment
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
                    entity_id="input_text.p1s_trays_used_this_print",
                    value=trays_str,
                )
            except Exception as e:
                self.log(
                    f"TRAY_TRACKING: Failed to update HA helper: {e}",
                    level="WARNING",
                )

    def _seed_active_trays(self):
        """Check all tray entities for currently active trays and seed tracking."""
        for tray_entity, slot in SLOT_BY_TRAY_ENTITY.items():
            try:
                active = self.get_state(tray_entity, attribute="active")
                if active is True or active == "true" or active == "True":
                    self._trays_used.add(slot)
                    self._open_active_segment(slot)
                    self._current_active_slot = slot
                    self.log(
                        f"TRAY_TRACKING_SEED slot={slot} entity={tray_entity}",
                        level="INFO",
                    )
            except Exception:
                pass

    def _on_tray_active_change(self, entity, attribute, old, new, kwargs):
        """Record when a tray becomes active during a print."""
        if not self._print_active:
            return

        slot = SLOT_BY_TRAY_ENTITY.get(entity)
        if slot is None:
            return

        is_active = new is True or new == "true" or new == "True"

        if is_active:
            if self._current_active_slot is not None and self._current_active_slot != slot:
                self._close_active_segment(self._current_active_slot)

            self._trays_used.add(slot)
            self._open_active_segment(slot)
            self._current_active_slot = slot
            self.log(
                f"TRAY_TRACKING_ACTIVE slot={slot} trays_used={self._trays_used}",
                level="DEBUG",
            )

    def _open_active_segment(self, slot):
        """Start a new time segment for a slot."""
        if slot not in self._tray_active_times:
            self._tray_active_times[slot] = []
        segments = self._tray_active_times[slot]
        if segments and segments[-1].get("end") is None:
            return
        segments.append({"start": datetime.datetime.utcnow(), "end": None})

    def _close_active_segment(self, slot):
        """Close the current time segment for a slot."""
        segments = self._tray_active_times.get(slot, [])
        if segments and segments[-1].get("end") is None:
            segments[-1]["end"] = datetime.datetime.utcnow()

    def _summarize_tray_times(self):
        """Return dict of {slot: total_seconds_active}."""
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
        """Return dict of {slot: proportion} based on active time.
        Returns empty dict if no time data available (fallback to equal split).
        """
        times = self._summarize_tray_times()
        total = sum(times.values())
        if total <= 0:
            return {}
        return {slot: round(t / total, 4) for slot, t in times.items()}

    # ── dedup persistence ─────────────────────────────────────────────

    def _load_seen_job_keys(self):
        """Load seen job_keys from disk. On missing/corrupt file, return empty OrderedDict."""
        try:
            with open(SEEN_JOBS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                keys = [str(k) for k in raw if k]
            elif isinstance(raw, dict):
                keys = [str(k) for k in raw if k]
            else:
                keys = []
            # Keep at most MAX_SEEN_JOBS (most recent when order matters)
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
        """Create data directory and empty seen_job_keys.json if missing (so path exists before first print)."""
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
        """Write current _seen_job_keys to disk. Creates directory if needed. On error, log and do not crash."""
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
        """Extract a dict from event data, handling HA native types.

        HA may pass the value as a native dict (from template rendering) or
        as a JSON string.  Returns a dict on success, or None on fatal error
        (with a log line written).
        """
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

    def _spoolman_use(self, spool_id, use_weight_g):
        """PUT /api/v1/spool/{id}/use  {"use_weight": grams}"""
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
