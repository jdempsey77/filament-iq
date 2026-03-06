"""
Spoolman Dropdown Sync — Populate input_select.spoolman_new_spool_filament from Spoolman API.

Fetches /api/v1/filament and sets dropdown options so "Add Spool" works without relying on
REST sensor root-array storage or command_line (which fails in HA Core container).

- On startup: fetch filaments, build options, call input_select.set_options.
- Listens for event SPOOLMAN_REFRESH_FILAMENT_DROPDOWN to refresh on demand (e.g. from script).
- On failure: log WARNING/ERROR and create persistent_notification.
"""

import json
import urllib.error
import urllib.request

import hassapi as hass

PLACEHOLDER = "— Select filament —"
DROPDOWN_ENTITY = "input_select.spoolman_new_spool_filament"
EVENT_REFRESH = "SPOOLMAN_REFRESH_FILAMENT_DROPDOWN"


def _vendor(f):
    if isinstance(f.get("vendor"), dict):
        return (f["vendor"].get("name") or "").strip()
    if isinstance(f.get("vendor_name"), str):
        return f["vendor_name"].strip()
    return ""


def _material(f):
    return (f.get("material") or "").strip()


def _name(f):
    return (f.get("name") or "").strip()


def _id_int(f):
    try:
        return int(f.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _label(f):
    """Build display label: id - vendor – material – name (omit empty parts)."""
    fid = f.get("id")
    if fid is None:
        fid = "?"
    else:
        fid = str(fid)
    vendor = _vendor(f)
    material = _material(f)
    name = _name(f)
    parts = [vendor, material, name]
    parts = [p for p in parts if p]
    rest = " – ".join(parts) if parts else fid
    return f"{fid} - {rest}"


def _sort_key(f):
    """(vendor, material, name, id_int) for stable sort; id_int avoids '10' before '2'."""
    return (_vendor(f).lower(), _material(f).lower(), _name(f).lower(), _id_int(f))


class SpoolmanDropdownSync(hass.Hass):
    def initialize(self):
        self.log("spoolman_dropdown_sync initializing", level="INFO")
        self.enabled = bool(self.args.get("enabled", True))
        if not self.enabled:
            self.log("Spoolman dropdown sync disabled (enabled=false).")
            return
        # TODO: Substitute YOUR_SPOOLMAN_IP with your Spoolman server IP. Port 7912 is Spoolman default.
        self.spoolman_base_url = str(
            self.args.get("spoolman_base_url", "http://YOUR_SPOOLMAN_IP:7912")
        ).rstrip("/")
        self.filament_url = f"{self.spoolman_base_url}/api/v1/filament"
        self._refresh_lock = False
        self._refresh_retry_scheduled = False
        self.listen_event(self._on_refresh_event, EVENT_REFRESH)
        self.run_in(self._wait_then_refresh, 0)
        self.log("Spoolman dropdown sync: listening for %s, startup refresh when entity ready", EVENT_REFRESH, level="INFO")

    def _on_refresh_event(self, event_name, data, kwargs):
        self.log("Spoolman dropdown sync: refresh requested via event %s", event_name, level="INFO")
        self._run_refresh(kwargs)

    def _wait_then_refresh(self, kwargs=None):
        """Wait until HA entity exists (up to 10 attempts, 1s delay), then run refresh."""
        kwargs = kwargs or {}
        attempt = kwargs.get("attempt", 0)
        if attempt >= 10:
            self.log("Spoolman dropdown sync: entity not ready after 10 attempts, running refresh anyway", level="WARNING")
            self._run_refresh(kwargs)
            return
        state = self.get_state(DROPDOWN_ENTITY)
        if state is not None:
            self._run_refresh(kwargs)
            return
        self.run_in(self._wait_then_refresh, 1, attempt=attempt + 1)

    def _run_refresh(self, kwargs=None):
        if self._refresh_lock:
            self.log("Spoolman dropdown sync: refresh already running, dropping request", level="DEBUG")
            if not self._refresh_retry_scheduled:
                self._refresh_retry_scheduled = True
                self.run_in(self._run_refresh, 2)
            return
        self._refresh_retry_scheduled = False
        self._refresh_lock = True
        try:
            try:
                filaments = self._fetch_filaments()
            except Exception as e:
                self.log("Spoolman dropdown sync: fetch failed: %s", e, level="ERROR")
                self._notify_error(str(e))
                return
            option_tuples = []
            for f in filaments:
                try:
                    label = _label(f)
                    if label and label != PLACEHOLDER:
                        option_tuples.append((label, _sort_key(f)))
                except Exception as e:
                    self.log("Spoolman dropdown sync: skip filament %s: %s", f.get("id"), e, level="WARNING")
            option_tuples.sort(key=lambda x: x[1])
            options = [PLACEHOLDER] + [t[0] for t in option_tuples]
            try:
                self.call_service(
                    "input_select/set_options",
                    entity_id=DROPDOWN_ENTITY,
                    options=options,
                )
                n = len(options) - 1
                self.log("Loaded %d filaments into %s" % (n, DROPDOWN_ENTITY), level="INFO")
            except Exception as e:
                self.log("Spoolman dropdown sync: set_options failed: %s", e, level="ERROR")
                self._notify_error("set_options: " + str(e))
        finally:
            self._refresh_lock = False

    def _fetch_filaments(self):
        req = urllib.request.Request(
            self.filament_url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read().decode()
                raw = json.loads(data)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Spoolman API {self.filament_url} HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Spoolman API {self.filament_url} URL error: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Spoolman API invalid JSON: {e}") from e
        if not isinstance(raw, list):
            raise RuntimeError(f"Spoolman API unexpected response type: {type(raw)}")
        return raw

    def _notify_error(self, message):
        try:
            self.call_service(
                "persistent_notification/create",
                title="Spoolman filament dropdown",
                message=f"Endpoint: {self.filament_url}\nError: {message}",
            )
        except Exception as e:
            self.log("Spoolman dropdown sync: could not create notification: %s", e, level="WARNING")
