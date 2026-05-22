"""
RunoutTracker — mid-print spool runout detection.

Writes per-slot boolean state to input_boolean.ams_slot_N_ran_out when a spool
goes empty during an active print. Cleared on any terminal print status transition.
The Lovelace card reads these entities to display a 🪫 badge.
"""

import hassapi as hass

from .base import FilamentIQBase, build_slot_mappings, TERMINAL_PRINT_STATES


class RunoutTracker(FilamentIQBase):

    def initialize(self):
        self._validate_config(
            required_keys=["printer_serial"],
            typed_keys={
                "startup_delay_seconds": (int, 60),
            },
        )

        prefix = self._build_entity_prefix()
        ams_units = self.args.get("ams_units")
        (
            self._tray_entity_by_slot,
            _,
            _,
            _,
        ) = build_slot_mappings(prefix, ams_units)

        self._print_status_entity = f"sensor.{prefix}_print_status"
        self._in_print = False
        self._all_slots = sorted(self._tray_entity_by_slot.keys())

        # Probe: helpers must exist before listeners are registered
        probe_entity = "input_boolean.ams_slot_1_ran_out"
        if self.get_state(probe_entity) is None:
            self.log(f"PROBE_MISSING entity={probe_entity}", level="ERROR")
            return

        for slot, entity_id in self._tray_entity_by_slot.items():
            self.listen_state(self._on_tray_state_change, entity_id, attribute="all")

        self.listen_state(self._on_print_status_change, self._print_status_entity)

        startup_delay = int(self.args.get("startup_delay_seconds", 60))
        self.run_in(self._startup_init, startup_delay)

        self.log(
            f"RunoutTracker initialized  slots={self._all_slots}  "
            f"startup_delay={startup_delay}s",
            level="INFO",
        )

    def _startup_init(self, kwargs):
        """Read print_status and prime or clear all ran-out booleans."""
        try:
            status = str(self.get_state(self._print_status_entity) or "").strip().lower()
        except Exception:
            status = ""

        if status in ("running", "printing"):
            self._in_print = True
            for slot, entity_id in sorted(self._tray_entity_by_slot.items()):
                full = self.get_state(entity_id, attribute="all") or {}
                attrs = full.get("attributes") or {}
                if attrs.get("empty") is True:
                    self._set_ran_out(slot, True)
                    self.log(f"STARTUP_PRIME slot={slot}", level="INFO")
        else:
            self._in_print = False
            self._clear_all()
            self.log("STARTUP_CLEARED", level="INFO")

    def _on_tray_state_change(self, entity, attribute, old, new, kwargs):
        if not self._in_print:
            return
        attrs = {}
        if isinstance(new, dict):
            attrs = new.get("attributes") or {}
        if attrs.get("empty") is not True:
            return
        slot = self._slot_for_entity(entity)
        if slot is None:
            return
        self._set_ran_out(slot, True)
        spool_id_str = self._spool_id_log_str(slot)
        self.log(
            f"RUNOUT_DETECTED slot={slot} entity={entity}{spool_id_str}",
            level="WARNING",
        )

    def _on_print_status_change(self, entity, attribute, old, new, kwargs):
        old_s = str(old or "").strip().lower()
        new_s = str(new or "").strip().lower()

        if old_s in ("running", "printing") and new_s in TERMINAL_PRINT_STATES:
            self._in_print = False
            self._clear_all()
            self.log(
                f"RUNOUT_CLEARED reason={new_s} slots_cleared={len(self._all_slots)}",
                level="INFO",
            )
        elif new_s in ("running", "printing") and old_s not in ("running", "printing"):
            self._in_print = True
            self.log("PRINT_STARTED_RUNOUT_TRACKING", level="INFO")

    def _slot_for_entity(self, entity_id):
        for slot, eid in self._tray_entity_by_slot.items():
            if eid == entity_id:
                return slot
        return None

    def _bool_entity(self, slot):
        return f"input_boolean.ams_slot_{slot}_ran_out"

    def _set_ran_out(self, slot, on):
        service = "input_boolean/turn_on" if on else "input_boolean/turn_off"
        try:
            self.call_service(service, entity_id=self._bool_entity(slot))
        except Exception as exc:
            self.log(f"RUNOUT_BOOL_FAILED slot={slot} on={on} error={exc}", level="WARNING")

    def _clear_all(self):
        for slot in self._all_slots:
            self._set_ran_out(slot, False)

    def _spool_id_log_str(self, slot):
        try:
            val = self.get_state(f"input_text.ams_slot_{slot}_spool_id")
            if val and str(val).strip() not in ("", "0", "unknown", "unavailable"):
                return f" spool_id={val}"
        except Exception:
            pass
        return ""
