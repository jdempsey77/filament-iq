"""
niimbot_printer.py — prints swatch labels on NIIMBOT D11_H via ska.

Listens for HA event `filament_iq_print_niimbot_label` with payload { spool_id: int }.
Fetches spool from Spoolman, then writes spool_id to
input_text.filament_iq_niimbot_print_queue. The ska monitor polls that helper
and runs print_niimbot.sh, which fetches spool data and renders the label locally.

Fires HA event `filament_iq_niimbot_label_result` with { spool_id, success, error }.

Config keys: spoolman_url, filament_profiles_path, dry_run.
"""

import json
import logging
import urllib.error
import urllib.request

from .base import FilamentIQBase
from .filament_profiles import FilamentProfilesClient, get_profiles_client

logger = logging.getLogger(__name__)

HELPER_ENTITY = "input_text.filament_iq_niimbot_print_queue"


class NiimbotPrinter(FilamentIQBase):

    def initialize(self):
        self._validate_config(["spoolman_url"])

        self.spoolman_url = str(self.args.get("spoolman_url", "")).rstrip("/")
        self.dry_run = bool(self.args.get("dry_run", True))

        profiles_path = self.args.get("filament_profiles_path")
        self.profiles_client = get_profiles_client(str(profiles_path)) if profiles_path else None

        self.listen_event(self._on_print_niimbot_event, "filament_iq_print_niimbot_label")
        self.log(
            f"NiimbotPrinter initialized spoolman={self.spoolman_url} dry_run={self.dry_run}",
            level="INFO",
        )

    def _on_print_niimbot_event(self, event_name, data, kwargs):
        """Handle filament_iq_print_niimbot_label event."""
        payload = data or {}
        spool_id = int(payload.get("spool_id", 0))
        if spool_id <= 0:
            self.log(f"NIIMBOT_SKIP invalid spool_id={spool_id}", level="WARNING")
            self._fire_result(spool_id, False, "Invalid spool_id")
            return

        try:
            spool_data = self._fetch_spool(spool_id)
            if spool_data is None:
                self._fire_result(spool_id, False, "Spool not found in Spoolman")
                return

            queue_value = str(spool_id)
            self.log(
                f"NIIMBOT_PRINT_QUEUE spool_id={spool_id} payload=spool_id",
                level="INFO",
            )

            if self.dry_run:
                self.log(
                    f"DRY_RUN: would set {HELPER_ENTITY}={queue_value} for spool {spool_id}",
                    level="INFO",
                )
            else:
                self.set_state(HELPER_ENTITY, state=queue_value)

            self._fire_result(spool_id, True)

        except Exception as e:
            self.log(f"NIIMBOT_ERROR spool_id={spool_id}: {e}", level="ERROR")
            self._fire_result(spool_id, False, str(e))

    def _fetch_spool(self, spool_id):
        """GET spool from Spoolman. Returns dict or None."""
        url = f"{self.spoolman_url}/api/v1/spool/{spool_id}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self.log(f"NIIMBOT_FETCH_SPOOL_FAILED spool_id={spool_id}: {e}", level="ERROR")
            return None

    def _fire_result(self, spool_id, success, error=None):
        """Fire filament_iq_niimbot_label_result HA event."""
        event_data = {"spool_id": spool_id, "success": success, "error": error}
        try:
            self.fire_event("filament_iq_niimbot_label_result", **event_data)
            self.log(
                f"NIIMBOT_RESULT spool_id={spool_id} success={success}"
                + (f" error={error}" if error else ""),
                level="INFO",
            )
        except Exception as e:
            self.log(f"NIIMBOT_RESULT_FIRE_FAILED: {e}", level="ERROR")
