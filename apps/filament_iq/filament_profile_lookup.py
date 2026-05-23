"""
filament_profile_lookup.py — Backend verification layer for filament profile matching.

Handles lookup requests and verification writes via HA events.
Stores results in profile_verifications.json.

Events handled:
  filament_iq_profile_lookup_request  { request_id, filament_id }
  filament_iq_profile_verify          { filament_id, action, [profile_id, profile_url, profile_name] }

Events fired:
  filament_iq_profile_lookup_response
  filament_iq_profile_verify_result

Config keys: spoolman_url (required), filament_profiles_path (required),
             verifications_path (optional).
"""

import json
import os
import urllib.request
from datetime import datetime

from .base import FilamentIQBase
from .filament_profiles import get_profiles_client

DEFAULT_VERIFICATIONS_PATH = (
    "/addon_configs/a0d7b954_appdaemon/data/filament_iq/profile_verifications.json"
)


class FilamentProfileLookup(FilamentIQBase):

    def initialize(self):
        self._validate_config(["spoolman_url", "filament_profiles_path"])

        self.spoolman_url = str(self.args.get("spoolman_url", "")).rstrip("/")
        profiles_path = self.args.get("filament_profiles_path")
        self.profiles_client = get_profiles_client(str(profiles_path))
        self.verifications_path = str(
            self.args.get("verifications_path", DEFAULT_VERIFICATIONS_PATH)
        )

        self.listen_event(
            self._on_lookup_request, "filament_iq_profile_lookup_request"
        )
        self.listen_event(self._on_verify, "filament_iq_profile_verify")
        self.listen_event(
            self._on_bulk_status_request,
            "filament_iq_profile_bulk_status_request"
        )
        self.log(
            f"FilamentProfileLookup initialized spoolman={self.spoolman_url} "
            f"verifications={self.verifications_path}",
            level="INFO",
        )

    # ── Event handlers ────────────────────────────────────────────────

    def _on_bulk_status_request(self, event_name, data, kwargs):
        """Return all known verification statuses from profile_verifications.json."""
        payload = data or {}
        request_id = str(payload.get("request_id", ""))
        try:
            verifications = self._read_verifications()
            statuses = {
                fid: entry.get("status", "unknown")
                for fid, entry in verifications.get("filaments", {}).items()
            }
        except Exception as e:
            self.log(f"BULK_STATUS_ERROR: {e}", level="ERROR")
            statuses = {}
        self.fire_event(
            "filament_iq_profile_bulk_status_response",
            request_id=request_id,
            statuses=statuses,
        )
        self.log(
            f"BULK_STATUS_RESPONSE request_id={request_id} count={len(statuses)}",
            level="INFO",
        )

    def _on_lookup_request(self, event_name, data, kwargs):
        payload = data or {}
        request_id = str(payload.get("request_id", ""))
        try:
            filament_id = int(payload.get("filament_id", 0))
        except (TypeError, ValueError):
            filament_id = 0

        if filament_id <= 0:
            self.log(
                f"PROFILE_LOOKUP_SKIP invalid filament_id={filament_id}",
                level="WARNING",
            )
            return

        verifications = self._read_verifications()
        entry = verifications.get("filaments", {}).get(str(filament_id))

        if entry:
            status = entry.get("status")
            if status == "verified":
                self._fire_lookup_response(
                    request_id=request_id,
                    filament_id=filament_id,
                    matched=True,
                    confidence="high",
                    status="verified",
                    profile_id=entry.get("profile_id"),
                    profile_url=entry.get("profile_url"),
                    profile_name=entry.get("profile_name"),
                )
                return
            if status == "no_profile_exists":
                self._fire_lookup_response(
                    request_id=request_id,
                    filament_id=filament_id,
                    matched=False,
                    confidence="none",
                    status="no_profile_exists",
                    profile_id=None,
                    profile_url=None,
                    profile_name=None,
                )
                return

        # Run scorer
        filament = self._fetch_filament(filament_id)
        if filament is None:
            self.log(
                f"PROFILE_LOOKUP_FETCH_FAILED filament_id={filament_id}",
                level="WARNING",
            )
            self._fire_lookup_response(
                request_id=request_id,
                filament_id=filament_id,
                matched=False,
                confidence="none",
                status="unverified",
                profile_id=None,
                profile_url=None,
                profile_name=None,
            )
            return

        vendor = str((filament.get("vendor") or {}).get("name") or "")
        material = str(filament.get("material") or "")
        name = str(filament.get("name") or "")

        profile = self.profiles_client.lookup(vendor, material, name)

        if profile.matched and profile.profile_id is not None:
            profile_url = (
                f"https://3dfilamentprofiles.com/filament/details/{profile.profile_id}"
            )
            profile_name = f"{vendor} · {material} · {name}"
            self._fire_lookup_response(
                request_id=request_id,
                filament_id=filament_id,
                matched=True,
                confidence=profile.confidence,
                status="candidate",
                profile_id=profile.profile_id,
                profile_url=profile_url,
                profile_name=profile_name,
            )
        else:
            self._fire_lookup_response(
                request_id=request_id,
                filament_id=filament_id,
                matched=False,
                confidence=profile.confidence,
                status="unverified",
                profile_id=None,
                profile_url=None,
                profile_name=None,
            )

    def _on_verify(self, event_name, data, kwargs):
        payload = data or {}
        try:
            filament_id = int(payload.get("filament_id", 0))
        except (TypeError, ValueError):
            filament_id = 0
        action = str(payload.get("action", ""))

        if filament_id <= 0 or action not in ("confirm", "reject", "no_match"):
            self.log(
                f"PROFILE_VERIFY_SKIP invalid filament_id={filament_id} action={action}",
                level="WARNING",
            )
            self._fire_verify_result(filament_id, action, False, "Invalid payload")
            return

        try:
            verifications = self._read_verifications()
            filaments = verifications.setdefault("filaments", {})
            now = datetime.utcnow().isoformat() + "Z"

            if action == "confirm":
                filaments[str(filament_id)] = {
                    "status": "verified",
                    "profile_id": payload.get("profile_id"),
                    "profile_url": str(payload.get("profile_url") or ""),
                    "profile_name": str(payload.get("profile_name") or ""),
                    "verified_at": now,
                    "scorer_version": "1.0",
                }
            elif action == "reject":
                filaments.pop(str(filament_id), None)
            elif action == "no_match":
                filaments[str(filament_id)] = {
                    "status": "no_profile_exists",
                    "profile_id": None,
                    "profile_url": None,
                    "profile_name": None,
                    "verified_at": now,
                    "scorer_version": "1.0",
                }

            verifications["filaments"] = filaments
            self._write_verifications(verifications)
            self._fire_verify_result(filament_id, action, True)

        except Exception as exc:
            self.log(f"PROFILE_VERIFY_FAILED: {exc}", level="ERROR")
            self._fire_verify_result(filament_id, action, False, str(exc))

    # ── File I/O ──────────────────────────────────────────────────────

    def _read_verifications(self) -> dict:
        """Read profile_verifications.json. Returns bare dict on any error."""
        try:
            with open(self.verifications_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {"version": 1, "filaments": {}}
        except Exception as exc:
            self.log(
                f"PROFILE_VERIFICATIONS_READ_FAILED: {exc}", level="WARNING"
            )
            return {"version": 1, "filaments": {}}

    def _write_verifications(self, data: dict) -> None:
        """Write profile_verifications.json atomically via .tmp + os.replace."""
        tmp_path = self.verifications_path + ".tmp"
        try:
            dir_path = os.path.dirname(self.verifications_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp_path, self.verifications_path)
        except Exception as exc:
            self.log(f"PROFILE_VERIFICATIONS_WRITE_FAILED: {exc}", level="ERROR")

    # ── Spoolman fetch ────────────────────────────────────────────────

    def _fetch_filament(self, filament_id: int):
        """GET filament from Spoolman. Returns dict or None."""
        url = f"{self.spoolman_url}/api/v1/filament/{filament_id}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            self.log(
                f"PROFILE_FETCH_FILAMENT_FAILED filament_id={filament_id}: {exc}",
                level="ERROR",
            )
            return None

    # ── Event helpers ─────────────────────────────────────────────────

    def _fire_lookup_response(self, *, request_id, filament_id, matched,
                               confidence, status, profile_id, profile_url,
                               profile_name):
        try:
            self.fire_event(
                "filament_iq_profile_lookup_response",
                request_id=request_id,
                filament_id=filament_id,
                matched=matched,
                confidence=confidence,
                status=status,
                profile_id=profile_id,
                profile_url=profile_url,
                profile_name=profile_name,
            )
        except Exception as exc:
            self.log(
                f"PROFILE_LOOKUP_RESPONSE_FIRE_FAILED: {exc}", level="ERROR"
            )

    def _fire_verify_result(self, filament_id, action, success, error=None):
        try:
            self.fire_event(
                "filament_iq_profile_verify_result",
                filament_id=filament_id,
                action=action,
                success=success,
                error=error,
            )
        except Exception as exc:
            self.log(
                f"PROFILE_VERIFY_RESULT_FIRE_FAILED: {exc}", level="ERROR"
            )
