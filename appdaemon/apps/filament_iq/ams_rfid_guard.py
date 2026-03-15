"""
RFID Guard — AppDaemon auditor enforcing RFID determinism invariants.

All config from self.args. No hardcoded instance-specific values.
"""

import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import hassapi as hass

from .base import FilamentIQBase

BAMBU_VENDOR_NAMES = ("bambu", "bambu lab")
_AMS_LOC_RE = re.compile(r"^AMS\d+_Slot\d+$", re.IGNORECASE)


class ReasonCode:
    RFID_TAG_MANUAL = "RFID_TAG_MANUAL"
    RFID_FILAMENT_MANUAL = "RFID_FILAMENT_MANUAL"
    ALL = (RFID_TAG_MANUAL, RFID_FILAMENT_MANUAL)

    @classmethod
    def resolve(cls, value):
        return value if value in cls.ALL else "UNKNOWN"


class AmsRfidGuard(FilamentIQBase):
    def initialize(self):
        self._validate_config(
            required_keys=["spoolman_url"],
            typed_keys={
                "scan_interval_seconds": (int, 300),
                "notify_cooldown_minutes": (int, 360),
                "dry_run": (bool, False),
            },
            range_keys={
                "scan_interval_seconds": (1, None),
                "notify_cooldown_minutes": (0, None),
            },
        )

        self.log("ams_rfid_guard VERSION=1.0.0", level="INFO")
        self.enabled = bool(self.args.get("enabled", True))
        if not self.enabled:
            self.log("RFID Guard disabled by config (enabled=false).")
            return

        self.spoolman_base_url = str(
            self.args.get("spoolman_url", self.args.get("spoolman_base_url", ""))
        ).rstrip("/")
        self.scan_interval_seconds = int(self.args.get("scan_interval_seconds", 300))
        self.dry_run = bool(self.args.get("dry_run", False))
        self.notify_cooldown_minutes = int(self.args.get("notify_cooldown_minutes", 360))
        self.cache_sensor = str(
            self.args.get("cache_sensor_entity", "sensor.spoolman_spools_cache")
        ).strip()
        self.use_cache_trigger = bool(self.args.get("use_cache_trigger", False))

        raw_patterns = self.args.get("rfid_managed_patterns", ["bambu", "bambu lab"])
        if isinstance(raw_patterns, str):
            raw_patterns = [p.strip() for p in raw_patterns.split(",") if p.strip()]
        self.rfid_managed_patterns = [
            re.compile(p, re.IGNORECASE) for p in raw_patterns if p
        ]

        mode = str(
            self.args.get("missing_ha_spool_uuid_mode", "warn_only")
        ).strip().lower()
        if mode not in ("warn_only", "quarantine"):
            mode = "warn_only"
        self.missing_ha_spool_uuid_mode = mode

        self._last_notify_by_key = {}

        self.run_every(
            self._run_scan,
            self.datetime() + datetime.timedelta(seconds=30),
            self.scan_interval_seconds,
        )
        if self.cache_sensor and self.use_cache_trigger:
            self.listen_state(self._on_cache_change, self.cache_sensor)
            self.log(f"RFID Guard listening to cache: {self.cache_sensor}")
        self.log(
            f"RFID Guard initialized interval={self.scan_interval_seconds}s "
            f"dry_run={self.dry_run} "
            f"missing_ha_uuid_mode={self.missing_ha_spool_uuid_mode} "
            f"patterns={raw_patterns} "
            f"notify_cooldown={self.notify_cooldown_minutes}min"
        )

    def _on_cache_change(self, entity, attribute, old, new, kwargs):
        self._run_scan({})

    def _fetch_spools(self):
        for path in ["/api/v1/spool?limit=1000", "/api/v1/spool"]:
            try:
                resp = self._spoolman_get(path)
                items = (
                    resp.get("items", [])
                    if isinstance(resp, dict)
                    else (resp if isinstance(resp, list) else [])
                )
                if not isinstance(items, list):
                    items = []
                self.log(
                    f"RFID_GUARD fetch_spools used endpoint {path} count={len(items)}",
                    level="INFO",
                )
                return items
            except Exception as exc:
                self.log(
                    f"RFID_GUARD fetch_spools {path} failed: {exc}",
                    level="DEBUG",
                )
                continue
        self.log(
            "RFID_GUARD fetch_spools all endpoints failed (Spoolman unreachable)",
            level="WARNING",
        )
        return []

    def _run_scan(self, kwargs):
        if not self.enabled:
            return
        try:
            items = self._fetch_spools()
        except Exception as exc:
            self.log(f"RFID_GUARD scan failed to fetch spools: {exc}", level="WARNING")
            return
        if not isinstance(items, list):
            items = []
        scanned = 0
        quarantined = 0
        skipped_quarantined = 0
        violations = 0
        warned_only = 0
        for spool in items:
            spool_id = self._safe_int(spool.get("id"), 0)
            if spool_id <= 0:
                continue
            scanned += 1
            if self._is_quarantined(spool):
                skipped_quarantined += 1
                continue
            violation = self._check_violation(spool)
            if violation:
                violations += 1
                if violation.get("warn_only"):
                    warned_only += 1
                    self.log(
                        f"RFID_GUARD WARN_ONLY spool_id={spool_id} "
                        f"reason={violation['reason']} "
                        f"filament={violation.get('filament_name','')} "
                        f"tag_uid={violation.get('tag_uid','')} "
                        f"ha_spool_uuid={violation.get('ha_spool_uuid','')} "
                        f"mode={self.missing_ha_spool_uuid_mode}",
                        level="WARNING",
                    )
                    self._maybe_notify(spool_id, violation)
                elif self.dry_run:
                    violation["dry_run"] = True
                    self.log(
                        f"RFID_GUARD DRY_RUN would quarantine spool_id={spool_id} "
                        f"reason={violation['reason']} "
                        f"filament={violation.get('filament_name','')} "
                        f"tag_uid={violation.get('tag_uid','')} "
                        f"ha_spool_uuid={violation.get('ha_spool_uuid','')}",
                        level="WARNING",
                    )
                    self._maybe_notify(spool_id, violation)
                else:
                    ok = self._quarantine_spool(spool, violation)
                    if ok:
                        quarantined += 1
        if scanned > 0:
            self.log(
                f"RFID_GUARD scan complete total={scanned} violations={violations} "
                f"quarantined={quarantined} dry_run={self.dry_run}",
                level="INFO",
            )

    def _is_quarantined(self, spool):
        loc = str(spool.get("location", "") or "").strip().upper()
        return loc == "QUARANTINE"

    def _get_tag_uid(self, extra):
        if not isinstance(extra, dict):
            return ""
        raw = extra.get("rfid_tag_uid") or extra.get("rfid_uid")
        s = self._json_text_to_str(raw)
        if not s:
            return ""
        return s.strip().upper()

    def _get_ha_spool_uuid(self, extra):
        if not isinstance(extra, dict):
            return ""
        raw = extra.get("ha_spool_uuid") or extra.get("ha_uuid")
        s = self._json_text_to_str(raw)
        return s.strip() if s else ""

    def _check_violation(self, spool):
        extra = spool.get("extra", {}) or {}
        if not isinstance(extra, dict):
            extra = {}
        tag_uid = self._get_tag_uid(extra)
        ha_spool_uuid = self._get_ha_spool_uuid(extra)
        filament = spool.get("filament", {}) or {}
        if not isinstance(filament, dict):
            filament = {}
        filament_name = str(filament.get("name", "") or "")
        spool_id = self._safe_int(spool.get("id"), 0)
        location = str(spool.get("location", "") or "")

        def _make_violation(reason, violation_text, expected_text):
            return {
                "reason": reason,
                "violation": violation_text,
                "expected": expected_text,
                "found": f"ha_spool_uuid={ha_spool_uuid or '(empty)'}",
                "tag_uid": tag_uid or "(none)",
                "ha_spool_uuid": ha_spool_uuid or "(empty)",
                "filament_name": filament_name,
                "spool_id": spool_id,
                "location": location,
            }

        if not _AMS_LOC_RE.match(location):
            return None

        lot_nr = str(spool.get("lot_nr") or "").strip()
        has_identity = bool(ha_spool_uuid or lot_nr)

        if tag_uid and not has_identity:
            violation = _make_violation(
                ReasonCode.RFID_TAG_MANUAL,
                "Spool has rfid_tag_uid but no identity (lot_nr or ha_spool_uuid)",
                "lot_nr or ha_spool_uuid must be non-empty",
            )
            if self.missing_ha_spool_uuid_mode == "warn_only":
                violation["warn_only"] = True
            return violation

        if self._is_rfid_managed_filament(filament) and not has_identity:
            violation = _make_violation(
                ReasonCode.RFID_FILAMENT_MANUAL,
                "Spool has Bambu/RFID-managed filament but no identity (lot_nr or ha_spool_uuid)",
                "lot_nr or ha_spool_uuid must be non-empty",
            )
            if self.missing_ha_spool_uuid_mode == "warn_only":
                violation["warn_only"] = True
            return violation

        return None

    def _is_rfid_managed_filament(self, filament):
        if not isinstance(filament, dict):
            return False
        extra = filament.get("extra", {}) or {}
        if isinstance(extra, dict) and extra.get("rfid_managed") is True:
            return True
        vendor_parts = []
        for key in ("vendor", "manufacturer", "brand"):
            v = filament.get(key)
            if isinstance(v, dict):
                vendor_parts.append(
                    str(v.get("name", v.get("manufacturer_name", "")) or "").lower()
                )
            elif v is not None:
                vendor_parts.append(str(v).lower())
        vendor_haystack = " ".join(vendor_parts)
        for bambu_name in BAMBU_VENDOR_NAMES:
            if bambu_name in vendor_haystack:
                return True
        name = str(filament.get("name", "") or "").lower()
        for pat in self.rfid_managed_patterns:
            if pat.search(name):
                return True
        return False

    def _quarantine_spool(self, spool, violation):
        spool_id = self._safe_int(spool.get("id"), 0)
        if spool_id <= 0:
            return False
        reason = ReasonCode.resolve(violation.get("reason", ""))

        self.log(
            f"RFID_GUARD quarantine spool_id={spool_id} "
            f"filament={violation.get('filament_name','')} "
            f"location={violation.get('location','')} "
            f"tag_uid={violation.get('tag_uid','')} "
            f"ha_spool_uuid={violation.get('ha_spool_uuid','')} "
            f"reason={reason}",
            level="WARNING",
        )
        try:
            self._spoolman_patch(
                f"/api/v1/spool/{spool_id}", {"location": "QUARANTINE"}
            )
        except Exception as exc:
            self.log(
                f"RFID_GUARD quarantine PATCH failed spool_id={spool_id}: {exc}",
                level="ERROR",
            )
            return False

        self._maybe_notify(spool_id, violation)
        return True

    def _maybe_notify(self, spool_id, violation):
        reason = ReasonCode.resolve(violation.get("reason", ""))
        dry_suffix = ":dryrun" if violation.get("dry_run") else ""
        key = f"{spool_id}:{reason}{dry_suffix}"
        now = self.datetime()
        last = self._last_notify_by_key.get(key)
        cooldown_seconds = self.notify_cooldown_minutes * 60
        if last is not None:
            delta = (now - last).total_seconds()
            if delta < cooldown_seconds:
                return
        self._last_notify_by_key[key] = now
        dry_marker = " [DRY_RUN]" if violation.get("dry_run") else ""
        title = (
            f"RFID Guard: Spool {spool_id} Violation (Warn Only){dry_marker}"
            if violation.get("warn_only")
            else f"RFID Guard: Spool {spool_id} Quarantined{dry_marker}"
        )
        reason_display = (
            f"{reason} (DRY_RUN)" if violation.get("dry_run") else reason
        )
        msg = (
            f"Spool ID: {spool_id}\n"
            f"Filament: {violation.get('filament_name', '')}\n"
            f"Reason: {reason_display}\n"
            f"Violation: {violation.get('violation', '')}\n"
            f"Expected: {violation.get('expected', '')}\n"
            f"Found: {violation.get('found', '')}"
        )
        try:
            nid_suffix = (
                f"dryrun_{reason}" if violation.get("dry_run") else reason
            )
            self.call_service(
                "persistent_notification/create",
                title=title,
                message=msg,
                notification_id=f"rfid_guard_quarantine_{spool_id}_{nid_suffix}",
            )
        except Exception as exc:
            self.log(
                f"RFID_GUARD notify failed spool_id={spool_id}: {exc}",
                level="WARNING",
            )

    def _json_text_to_str(self, v):
        if v is None:
            return ""
        s = str(v).strip()
        if not s:
            return ""
        try:
            out = json.loads(s)
            return "" if out is None else str(out)
        except Exception:
            return str(s).strip('"')

    def _spoolman_get(self, path):
        url = urllib.parse.urljoin(
            self.spoolman_base_url + "/", path.lstrip("/")
        )
        req = urllib.request.Request(
            url, method="GET", headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}

    def _spoolman_patch(self, path, payload):
        url = urllib.parse.urljoin(
            self.spoolman_base_url + "/", path.lstrip("/")
        )
        req = urllib.request.Request(
            url,
            method="PATCH",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}

    def _safe_int(self, value, default=0):
        try:
            return int(str(value).strip())
        except (ValueError, TypeError, AttributeError):
            return default
