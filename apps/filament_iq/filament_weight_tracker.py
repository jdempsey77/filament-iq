"""
Filament Weight Delta Tracker — Snapshots spool weights before/after prints.

All config from self.args. Entity naming uses _build_entity_prefix().
"""

import datetime
import json
import urllib.request

import hassapi as hass

from .base import FilamentIQBase


class FilamentWeightTracker(FilamentIQBase):
    def initialize(self):
        self._validate_config(["spoolman_url", "printer_serial"])

        self.log("FilamentWeightTracker initialized", level="INFO")
        self.spoolman_url = str(
            self.args.get("spoolman_url", "")
        ).rstrip("/")
        self.report_path = str(
            self.args.get(
                "report_path",
                "/config/filament_weight_reports.log",
            )
        )
        self._before_snapshot = None
        self._before_timestamp = None
        self._print_name = None

        prefix = self._build_entity_prefix()
        self._operator_status_entity = str(
            self.args.get(
                "operator_status_entity",
                "sensor.filament_iq_operator_status",
            )
        ).strip()
        self._weight_snapshot_button_entity = str(
            self.args.get(
                "weight_snapshot_button_entity",
                "input_button.filament_iq_weight_snapshot_now",
            )
        ).strip()
        print_entities = self.args.get("print_name_entities")
        if print_entities:
            self._print_name_entities = list(print_entities)
        else:
            # Default: read from printer entities (Bambu integration)
            printer_print_status = self.args.get("printer_print_status_entity")
            printer_current_stage = self.args.get("printer_current_stage_entity")
            if printer_print_status or printer_current_stage:
                self._print_name_entities = [
                    e for e in [printer_current_stage, printer_print_status] if e
                ]
            else:
                self._print_name_entities = [
                    f"sensor.{prefix}_current_stage",
                    f"sensor.{prefix}_print_status",
                ]

        self.listen_state(
            self._on_print_start,
            self._operator_status_entity,
            new=lambda x: x in ("printing_normally", "printing"),
        )

        self.listen_state(
            self._on_print_end,
            self._operator_status_entity,
            new=lambda x: x in ("idle", "finished", "failed"),
        )

        self.listen_state(
            self._on_manual_snapshot,
            self._weight_snapshot_button_entity,
        )

    def _get_all_spool_weights(self):
        url = f"{self.spoolman_url}/api/v1/spool?limit=1000"
        req = urllib.request.Request(
            url, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self.log(
                f"WEIGHT_TRACKER: Failed to fetch spools: {e}",
                level="ERROR",
            )
            return None

        if isinstance(data, dict) and "items" in data:
            data = data["items"]
        if not isinstance(data, list):
            self.log(
                "WEIGHT_TRACKER: Unexpected response format",
                level="ERROR",
            )
            return None

        result = {}
        for spool in data:
            sid = int(spool.get("id") or 0)
            if sid <= 0:
                continue
            filament = spool.get("filament") or {}
            vendor = (filament.get("vendor") or {}).get("name", "")
            result[sid] = {
                "remaining_weight": float(spool.get("remaining_weight") or 0),
                "filament_name": filament.get("name", ""),
                "material": filament.get("material", ""),
                "vendor": vendor,
                "location": spool.get("location", ""),
            }
        return result

    def _get_print_name(self):
        for entity in self._print_name_entities:
            try:
                state = self.get_state(entity, attribute="all")
                if isinstance(state, dict):
                    name = (
                        state.get("attributes", {}).get("file", "")
                        or state.get("attributes", {}).get("subtask_name", "")
                    )
                    if name:
                        return str(name)
            except Exception:
                pass
        return "unknown_print"

    def _take_before_snapshot(self, reason="auto"):
        weights = self._get_all_spool_weights()
        if weights is None:
            self.log("WEIGHT_TRACKER: Before snapshot FAILED", level="ERROR")
            return False
        self._before_snapshot = weights
        self._before_timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        self._print_name = self._get_print_name()
        self.log(
            f"WEIGHT_TRACKER: Before snapshot taken reason={reason} "
            f"spools={len(weights)} print={self._print_name}",
            level="INFO",
        )
        return True

    def _take_after_snapshot_and_report(self, reason="auto"):
        if self._before_snapshot is None:
            self.log(
                "WEIGHT_TRACKER: No before snapshot — skipping report",
                level="WARNING",
            )
            return

        after_weights = self._get_all_spool_weights()
        if after_weights is None:
            self.log("WEIGHT_TRACKER: After snapshot FAILED", level="ERROR")
            return

        after_timestamp = datetime.datetime.utcnow().isoformat() + "Z"

        deltas = []
        for sid, before_data in self._before_snapshot.items():
            after_data = after_weights.get(sid)
            if after_data is None:
                continue
            before_g = before_data["remaining_weight"]
            after_g = after_data["remaining_weight"]
            delta_g = round(before_g - after_g, 1)
            if delta_g != 0:
                deltas.append(
                    {
                        "spool_id": sid,
                        "filament_name": before_data.get("filament_name", ""),
                        "material": before_data.get("material", ""),
                        "vendor": before_data.get("vendor", ""),
                        "location": before_data.get("location", ""),
                        "before_g": round(before_g, 1),
                        "after_g": round(after_g, 1),
                        "consumed_g": delta_g,
                    }
                )

        deltas.sort(key=lambda x: x["consumed_g"], reverse=True)

        total_consumed = round(sum(d["consumed_g"] for d in deltas), 1)

        report = {
            "print_name": self._print_name or "unknown",
            "reason": reason,
            "before_timestamp": self._before_timestamp,
            "after_timestamp": after_timestamp,
            "total_consumed_g": total_consumed,
            "spool_deltas": deltas,
            "spools_unchanged": len(self._before_snapshot) - len(deltas),
        }

        self.log(
            f"WEIGHT_TRACKER: Report — print={self._print_name} "
            f"total_consumed={total_consumed}g spools_changed={len(deltas)}",
            level="INFO",
        )
        for d in deltas:
            self.log(
                f"  spool_id={d['spool_id']} {d['vendor']} {d['filament_name']} "
                f"({d['material']}) @ {d['location']}: "
                f"{d['before_g']}g → {d['after_g']}g (consumed {d['consumed_g']}g)",
                level="INFO",
            )

        self._append_report(report)

        self._before_snapshot = None
        self._before_timestamp = None
        self._print_name = None

    def _append_report(self, report):
        try:
            with open(self.report_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(report, sort_keys=True) + "\n")
            self.log(
                f"WEIGHT_TRACKER: Report written to {self.report_path}",
                level="INFO",
            )
        except Exception as e:
            self.log(
                f"WEIGHT_TRACKER: Failed to write report: {e}",
                level="ERROR",
            )

    def _on_print_start(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        self._take_before_snapshot(reason="print_start")

    def _on_print_end(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        self.run_in(self._delayed_after_snapshot, 10, reason="print_end")

    def _delayed_after_snapshot(self, kwargs):
        self._take_after_snapshot_and_report(
            reason=kwargs.get("reason", "auto")
        )

    def _on_manual_snapshot(self, entity, attribute, old, new, kwargs):
        if not new or new == old:
            return
        if self._before_snapshot is None:
            success = self._take_before_snapshot(reason="manual")
            if success:
                self.call_service(
                    "persistent_notification/create",
                    title="Weight Tracker",
                    message=f"Before snapshot taken ({len(self._before_snapshot)} spools). "
                    "Press again after print for delta report.",
                    notification_id="weight_tracker_manual",
                )
        else:
            self._take_after_snapshot_and_report(reason="manual")
            self.call_service(
                "persistent_notification/create",
                title="Weight Tracker",
                message="Delta report generated. Check filament_weight_reports.log.",
                notification_id="weight_tracker_manual",
            )
