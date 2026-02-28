"""AMS Print Usage Sync — writes filament consumption to Spoolman after each print.

Triggered by custom HA event P1S_PRINT_USAGE_READY fired by the
p1s_remaining_snapshot_on_finish automation.

RFID slots:     consumption = start_g - end_g from fuel gauge snapshots.
Non-RFID slots: best-effort equal share of (print_weight_g - rfid_total_g).

Slot-to-spool mapping: input_text.ams_slot_{1-6}_spool_id (reconciler-owned, read-only).
Spoolman write:         PUT /api/v1/spool/{id}/use {"use_weight": grams}
Dedup:                  in-memory job_key set (capped at 50 entries).
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

import hassapi as hass

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
        self._seen_job_keys = OrderedDict()

        if not self.enabled:
            self.log("AmsPrintUsageSync disabled via config", level="WARNING")
            return

        self.listen_event(self._handle_usage_event, "P1S_PRINT_USAGE_READY")
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

        trays_used_raw = str(data.get("trays_used", "")).strip()
        start_json_raw = str(data.get("start_json", "{}")).strip()
        end_json_raw = str(data.get("end_json", "{}")).strip()

        # ── dedup ────────────────────────────────────────────────────
        if job_key and job_key in self._seen_job_keys:
            self.log(f"DEDUP_SKIP job_key={job_key}", level="INFO")
            return

        # ── parse JSON payloads ──────────────────────────────────────
        try:
            start_map = json.loads(start_json_raw) if start_json_raw else {}
        except (json.JSONDecodeError, TypeError):
            self.log(
                f"USAGE_SKIP reason=JSON_PARSE_ERROR field=start_json "
                f"raw={start_json_raw!r}",
                level="ERROR",
            )
            return
        try:
            end_map = json.loads(end_json_raw) if end_json_raw else {}
        except (json.JSONDecodeError, TypeError):
            self.log(
                f"USAGE_SKIP reason=JSON_PARSE_ERROR field=end_json "
                f"raw={end_json_raw!r}",
                level="ERROR",
            )
            return

        if not isinstance(start_map, dict):
            start_map = {}
        if not isinstance(end_map, dict):
            end_map = {}

        # ── guard: no start data = cancelled before print ────────────
        if not start_map:
            self.log(
                f"USAGE_SKIP reason=NO_START_SNAPSHOT job_key={job_key}",
                level="INFO",
            )
            return

        # ── build slot list ──────────────────────────────────────────
        active_slots = self._parse_trays_used(trays_used_raw, start_map)

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

            if start_g > 0:
                consumption_g = max(0.0, start_g - end_g)
                rfid_results.append((slot, spool_id, consumption_g))
            else:
                nonrfid_slots.append((slot, spool_id))

        # ── non-RFID allocation ──────────────────────────────────────
        rfid_total_g = sum(c for _, _, c in rfid_results)
        nonrfid_pool_g = max(0.0, print_weight_g - rfid_total_g)

        nonrfid_each_g = (
            nonrfid_pool_g / len(nonrfid_slots) if nonrfid_slots else 0.0
        )

        nonrfid_results = []
        for slot, spool_id in nonrfid_slots:
            self.log(
                f"USAGE_NONRFID_ESTIMATE slot={slot} spool_id={spool_id} "
                f"estimated_g={nonrfid_each_g:.1f} pool_g={nonrfid_pool_g:.1f} "
                f"slots={len(nonrfid_slots)}",
                level="INFO",
            )
            nonrfid_results.append((slot, spool_id, nonrfid_each_g))

        # ── write to Spoolman ────────────────────────────────────────
        patched = 0
        all_results = rfid_results + nonrfid_results

        for slot, spool_id, consumption_g in all_results:
            method = (
                "rfid_delta"
                if any(s == slot for s, _, _ in rfid_results)
                else "nonrfid_estimate"
            )

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

        # ── summary ──────────────────────────────────────────────────
        total_consumed = sum(c for _, _, c in all_results)
        self.log(
            f"USAGE_SUMMARY job_key={job_key} task={task_name} "
            f"status={print_status} "
            f"rfid_slots={len(rfid_results)} "
            f"nonrfid_slots={len(nonrfid_results)} "
            f"total_consumed_g={total_consumed:.1f} "
            f"patched={patched} skipped={skipped}",
            level="INFO",
        )

    # ── helpers ───────────────────────────────────────────────────────

    def _parse_trays_used(self, trays_used_raw, start_map):
        """Parse trays_used CSV into sorted list of slot numbers.

        Handles both slot numbers ("1,4") and legacy entity IDs.
        Falls back to slots present in start_map.
        """
        slots = set()
        for token in trays_used_raw.split(","):
            token = token.strip()
            if not token:
                continue
            if token in ("1", "2", "3", "4", "5", "6"):
                slots.add(int(token))
                continue
            if token in SLOT_BY_TRAY_ENTITY:
                slots.add(SLOT_BY_TRAY_ENTITY[token])
                continue
            try:
                n = int(token)
                if 1 <= n <= 6:
                    slots.add(n)
            except ValueError:
                pass

        if not slots:
            for k in start_map:
                try:
                    n = int(k)
                    if 1 <= n <= 6:
                        slots.add(n)
                except ValueError:
                    pass

        return sorted(slots)

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
