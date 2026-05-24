"""
filament_profile_lookup.py — Backend verification layer for filament profile matching.

Handles lookup requests and verification via HA events.
Spoolman is the source of truth for verification status: a filament is verified
if and only if its Spoolman extra.profile_url is a non-empty string.

Events handled:
  filament_iq_profile_lookup_request  { request_id, filament_id }
  filament_iq_profile_verify          { filament_id, action, [profile_id, profile_url, profile_name] }

Events fired:
  filament_iq_profile_lookup_response
  filament_iq_profile_verify_result

Config keys: spoolman_url (required), filament_profiles_path (required).
"""

import json
import urllib.request

import requests

from .base import FilamentIQBase
from .filament_profiles import get_profiles_client


class FilamentProfileLookup(FilamentIQBase):

    def initialize(self):
        self._validate_config(["spoolman_url", "filament_profiles_path"])

        self.spoolman_url = str(self.args.get("spoolman_url", "")).rstrip("/")
        profiles_path = self.args.get("filament_profiles_path")
        self.profiles_client = get_profiles_client(str(profiles_path))

        self.listen_event(
            self._on_lookup_request, "filament_iq_profile_lookup_request"
        )
        self.listen_event(self._on_verify, "filament_iq_profile_verify")
        self.listen_event(
            self._on_bulk_status_request,
            "filament_iq_profile_bulk_status_request"
        )
        self.log(
            f"FilamentProfileLookup initialized spoolman={self.spoolman_url}",
            level="INFO",
        )

    # ── Event handlers ────────────────────────────────────────────────

    def _on_bulk_status_request(self, event_name, data, kwargs):
        """Return verification statuses derived from Spoolman extra.profile_url."""
        payload = data or {}
        request_id = str(payload.get("request_id", ""))
        statuses = {}
        try:
            resp = requests.get(
                f"{self.spoolman_url}/api/v1/filament",
                timeout=10,
            )
            resp.raise_for_status()
            for filament in resp.json():
                fid = str(filament.get("id", ""))
                if not fid:
                    continue
                raw_url = ((filament.get("extra") or {}).get("profile_url") or "").strip('"')
                statuses[fid] = "verified" if raw_url else "unverified"
        except Exception as exc:
            self.log(f"BULK_STATUS_ERROR: {exc}", level="WARNING")
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

        # Return cached verification from Spoolman extra if present
        raw_url = ((filament.get("extra") or {}).get("profile_url") or "").strip('"')
        if raw_url:
            raw_name = ((filament.get("extra") or {}).get("profile_name") or "").strip('"')
            self._fire_lookup_response(
                request_id=request_id,
                filament_id=filament_id,
                matched=True,
                confidence="high",
                status="verified",
                profile_id=None,
                profile_url=raw_url,
                profile_name=raw_name,
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
            if action == "confirm":
                self._patch_spoolman_extra(filament_id, {
                    "profile_url": str(payload.get("profile_url") or ""),
                    "profile_name": str(payload.get("profile_name") or ""),
                })
            elif action in ("reject", "no_match"):
                self._patch_spoolman_extra(filament_id, {
                    "profile_url": None,
                    "profile_name": None,
                })
            self._fire_verify_result(filament_id, action, True)

        except Exception as exc:
            self.log(f"PROFILE_VERIFY_FAILED: {exc}", level="ERROR")
            self._fire_verify_result(filament_id, action, False, str(exc))

    # ── Spoolman extra patch ──────────────────────────────────────────

    def _patch_spoolman_extra(self, filament_id: int, extra_patch: dict) -> None:
        """Merge extra_patch into Spoolman filament extra. Never raises."""
        url = f"{self.spoolman_url}/api/v1/filament/{filament_id}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            existing_extra = resp.json().get("extra") or {}
            encoded_patch = {k: json.dumps(v, ensure_ascii=False) for k, v in extra_patch.items()}
            merged = {**existing_extra, **encoded_patch}
            patch_resp = requests.patch(url, json={"extra": merged}, timeout=10)
            patch_resp.raise_for_status()
            self.log(
                f"SPOOLMAN_EXTRA_PATCHED filament_id={filament_id} keys={list(extra_patch.keys())}",
                level="INFO",
            )
        except Exception as exc:
            self.log(
                f"SPOOLMAN_EXTRA_PATCH_FAILED filament_id={filament_id} error={exc}",
                level="WARNING",
            )

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
