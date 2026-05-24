"""
niimbot_printer.py — prints swatch labels on NIIMBOT D11_H via ska.

Listens for HA event `filament_iq_print_niimbot_label` with payload { spool_id: int }.
Fetches spool from Spoolman, then writes spool_id (or spool_id|profile_url when
the filament has a verified profile) to input_text.filament_iq_niimbot_print_queue.
The ska monitor polls that helper and runs print_niimbot.sh, which fetches spool data,
renders the label locally, and composites a QR code when profile_url is present.

Fires HA event `filament_iq_niimbot_label_result` with { spool_id, success, error }.

Config keys: spoolman_url, filament_profiles_path, verifications_path, dry_run.
"""

import json
import logging
import urllib.error
import urllib.request

from .base import FilamentIQBase
from .filament_profiles import FilamentProfilesClient, get_profiles_client

DEFAULT_VERIFICATIONS_PATH = (
    "/addon_configs/a0d7b954_appdaemon/data/filament_iq/profile_verifications.json"
)

logger = logging.getLogger(__name__)

HELPER_ENTITY = "input_text.filament_iq_niimbot_print_queue"


class NiimbotPrinter(FilamentIQBase):

    def initialize(self):
        self._validate_config(["spoolman_url"])

        self.spoolman_url = str(self.args.get("spoolman_url", "")).rstrip("/")
        self.dry_run = bool(self.args.get("dry_run", True))

        profiles_path = self.args.get("filament_profiles_path")
        self.profiles_client = get_profiles_client(str(profiles_path)) if profiles_path else None

        self.verifications_path = str(
            self.args.get("verifications_path", DEFAULT_VERIFICATIONS_PATH)
        )

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

            queue_value = self._build_queue_value(spool_id, spool_data)
            self.log(
                f"NIIMBOT_PRINT_QUEUE spool_id={spool_id} payload={queue_value}",
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

    def _build_queue_value(self, spool_id: int, spool_data: dict) -> str:
        """Return 'spool_id|profile_url' if verified, else 'spool_id'."""
        filament = spool_data.get("filament") or {}
        filament_id = filament.get("id")

        if filament_id is not None:
            profile_url, reason = self._lookup_profile_url(filament_id)
            if profile_url:
                self.log(
                    f"NIIMBOT_PROFILE_VERIFIED spool_id={spool_id} profile_url={profile_url}",
                    level="INFO",
                )
                return f"{spool_id}|{profile_url}"
            self.log(
                f"NIIMBOT_PROFILE_UNVERIFIED spool_id={spool_id} reason={reason}",
                level="INFO",
            )
        else:
            self.log(
                f"NIIMBOT_PROFILE_UNVERIFIED spool_id={spool_id} reason=missing",
                level="INFO",
            )
        return str(spool_id)

    def _lookup_profile_url(self, filament_id) -> tuple:
        """Return (profile_url, reason). profile_url is None when not verified."""
        try:
            url = f"{self.spoolman_url}/api/v1/filament/{filament_id}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                filament = json.loads(resp.read().decode("utf-8"))
            raw_url = ((filament.get("extra") or {}).get("profile_url") or "").strip('"')
            if raw_url:
                return raw_url, "verified"
            return None, "unverified"
        except Exception as exc:
            self.log(
                f"NIIMBOT_PROFILE_LOOKUP_FAILED filament_id={filament_id}: {exc}", level="WARNING"
            )
            return None, "error"

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
