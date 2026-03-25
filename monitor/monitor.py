#!/usr/bin/env python3
"""Filament IQ Monitor — HA availability + print lifecycle + system resource daemon.

Runs on ska as a systemd user unit. Three concurrent loops:
  1. HA availability monitor (polls /api/, detects outages, grabs logs on recovery)
  2. Print lifecycle monitor (state machine: IDLE→PREPARING→PRINTING→FINISHING→IDLE)
  3. System resource monitor (polls HA + local /proc, threshold alerts)

Artifacts written to NAS at ARTIFACT_ROOT. Secrets loaded from
~/.config/filament_iq/secrets.env. Config from monitor-config.env.

Python 3.12 stdlib only — no pip dependencies.
"""

import datetime
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────

# Slot → (ams_unit, tray_index) mapping. Slots 1-4 are AMS Pro unit 1,
# slot 5 is AMS HT unit 128, slot 6 is AMS HT unit 129.
_SLOT_TO_AMS = {
    1: ("1", 1), 2: ("1", 2), 3: ("1", 3), 4: ("1", 4),
    5: ("128", 1), 6: ("129", 1), 7: ("130", 1),
}

# Print lifecycle states
STATE_IDLE = "idle"
STATE_PREPARING = "preparing"
STATE_PRINTING = "printing"
STATE_FINISHING = "finishing"

SUCCESS_STATES = frozenset({"finish"})
FAILED_STATES = frozenset({"failed"})
TERMINAL_STATES = SUCCESS_STATES | FAILED_STATES

# HA System Monitor entity IDs for resource polling
HA_RESOURCE_ENTITIES = {
    "ha_cpu": "sensor.system_monitor_processor_use",
    "ha_mem": "sensor.system_monitor_memory_usage",
    "ha_disk": "sensor.system_monitor_disk_usage",
    "ha_swap": "sensor.system_monitor_swap_usage",
    "ha_temp": "sensor.system_monitor_processor_temperature",
}

# Alert cooldown
_ALERT_COOLDOWN_S = 3600  # 60 minutes

CONFIG_DIR = Path.home() / ".config" / "filament_iq"
SECRETS_FILE = CONFIG_DIR / "secrets.env"
CONFIG_FILE = CONFIG_DIR / "monitor-config.env"
STATE_FILE = CONFIG_DIR / "monitor_state.json"

log = logging.getLogger("filament_iq_monitor")


# ── Entity Builder ───────────────────────────────────────────────────

def _build_entities(printer_serial: str, ams_slots: list[int]) -> dict:
    """Build HA entity IDs from printer serial and slot list."""
    prefix = f"p1s_{printer_serial.lower()}"
    tray_entities = {}
    for s in ams_slots:
        ams_unit, tray_idx = _SLOT_TO_AMS[s]
        tray_entities[s] = f"sensor.{prefix}_ams_{ams_unit}_tray_{tray_idx}"
    return {
        "print_status": f"sensor.{prefix}_print_status",
        "task_name": f"sensor.{prefix}_task_name",
        "print_progress": f"sensor.{prefix}_print_progress",
        "active_tray": f"sensor.{prefix}_active_tray",
        "tray_entities": tray_entities,
        "slot_to_spool_entities": {
            s: f"input_text.ams_slot_{s}_spool_id" for s in ams_slots
        },
    }


# ── Config Loading ───────────────────────────────────────────────────

def _load_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from an env file. Strips quotes."""
    result = {}
    if not path.is_file():
        return result
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip("'\"")
            result[key.strip()] = value
    return result


class Config:
    """Validated configuration from env files."""

    def __init__(self):
        secrets = _load_env_file(SECRETS_FILE)
        config = _load_env_file(CONFIG_FILE)

        self.ha_token = secrets.get("HA_TOKEN") or secrets.get("HOME_ASSISTANT_TOKEN", "")
        if not self.ha_token:
            log.error("HA_TOKEN not set in %s", SECRETS_FILE)
            sys.exit(1)

        self.ha_url = config.get("HA_URL", "").rstrip("/")
        if not self.ha_url:
            log.error("HA_URL not set in %s", CONFIG_FILE)
            sys.exit(1)

        self.printer_serial = config.get("PRINTER_SERIAL", "").lower()
        if not self.printer_serial:
            log.error("PRINTER_SERIAL not set in %s", CONFIG_FILE)
            sys.exit(1)

        self.ams_slots = [int(s) for s in config.get("AMS_SLOTS", "1,2,3,4,5,6").split(",")]

        self.spoolman_url = config.get("SPOOLMAN_URL", "http://192.168.4.124:7912").rstrip("/")
        self.artifact_root = Path(config.get("ARTIFACT_ROOT", "/mnt/store/filament_iq/monitor"))
        self.ha_poll_interval = int(config.get("HA_POLL_INTERVAL", "30"))
        self.print_poll_interval = int(config.get("PRINT_POLL_INTERVAL", "10"))
        self.outage_poll_interval = int(config.get("OUTAGE_POLL_INTERVAL", "10"))
        self.log_retention_days = int(config.get("LOG_RETENTION_DAYS", "7"))
        self.appdaemon_addon_id = config.get("APPDAEMON_ADDON_ID", "a0d7b954_appdaemon")

        # Resource monitoring
        self.notify_service = config.get("NOTIFY_SERVICE", "mobile_app_jd_pixel_10_pro_xl")
        self.resource_poll_interval = int(config.get("RESOURCE_POLL_INTERVAL", "60"))
        self.resource_cpu_warn = float(config.get("RESOURCE_CPU_WARN", "80"))
        self.resource_mem_warn = float(config.get("RESOURCE_MEM_WARN", "85"))
        self.resource_disk_warn = float(config.get("RESOURCE_DISK_WARN", "85"))
        self.resource_swap_warn = float(config.get("RESOURCE_SWAP_WARN", "90"))
        self.resource_temp_warn_f = float(config.get("RESOURCE_TEMP_WARN_F", "158"))

        self.entities = _build_entities(self.printer_serial, self.ams_slots)


# ── HTTP Helpers ─────────────────────────────────────────────────────

def _ha_request(cfg: Config, path: str, timeout: float = 10.0) -> tuple[int, str]:
    """GET from HA API. Returns (status_code, body). Never raises on HTTP errors."""
    url = f"{cfg.ha_url}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg.ha_token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        return 0, str(e)


def _ha_get_state(cfg: Config, entity_id: str) -> dict | None:
    """Fetch a single entity state from HA. Returns parsed JSON or None."""
    code, body = _ha_request(cfg, f"/api/states/{entity_id}")
    if code != 200:
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


def _send_ha_notification(cfg: Config, title: str, message: str) -> None:
    """POST a mobile notification via HA notify service. Never raises."""
    url = f"{cfg.ha_url}/api/services/notify/{cfg.notify_service}"
    payload = json.dumps({"title": title, "message": message}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {cfg.ha_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            log.info("Notification sent: %s (HTTP %d)", title, resp.status)
    except Exception as e:
        log.warning("Notification failed: %s — %s", title, e)


def _spoolman_get(cfg: Config, path: str, timeout: float = 10.0) -> list | dict | None:
    """GET from Spoolman API. Returns parsed JSON or None."""
    url = f"{cfg.spoolman_url}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        log.warning("Spoolman request failed: %s %s", path, e)
        return None


# ── Artifact Writing ─────────────────────────────────────────────────

def _write_artifact(path: Path, data: dict) -> bool:
    """Write JSON artifact atomically. Returns True on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)
        log.info("Artifact written: %s", path)
        return True
    except Exception as e:
        log.warning("Artifact write failed: %s — %s", path, e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _write_text_artifact(path: Path, text: str) -> bool:
    """Write text artifact atomically."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except Exception as e:
        log.warning("Text artifact write failed: %s — %s", path, e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


# ── Artifact Log Setup ───────────────────────────────────────────────

def _setup_artifact_log(cfg: Config, name: str, filename: str) -> logging.Logger:
    """Create a rotating file logger for artifact logs."""
    alog = logging.getLogger(name)
    alog.setLevel(logging.INFO)
    alog.propagate = False
    try:
        log_dir = cfg.artifact_root
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            log_dir / filename,
            when="midnight",
            backupCount=cfg.log_retention_days,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        alog.addHandler(handler)
    except Exception as e:
        log.warning("Could not set up artifact log %s: %s", filename, e)
    return alog


# ── Spoolman Snapshot ────────────────────────────────────────────────

def _snapshot_spoolman_weights(cfg: Config) -> dict:
    """Snapshot all spool weights from Spoolman. Returns {slot: {spool_id, remaining_weight, location}}."""
    result = {}
    # Read slot→spool_id bindings from HA
    slot_bindings = {}
    for slot, entity in cfg.entities["slot_to_spool_entities"].items():
        state = _ha_get_state(cfg, entity)
        if state:
            try:
                spool_id = int(state.get("state", "0"))
                if spool_id > 0:
                    slot_bindings[slot] = spool_id
            except (ValueError, TypeError):
                pass

    # Fetch all spools from Spoolman
    spools_data = _spoolman_get(cfg, "/api/v1/spool?limit=1000")
    if spools_data is None:
        return result

    # Handle paginated response
    if isinstance(spools_data, dict) and "items" in spools_data:
        spools_list = spools_data["items"]
    elif isinstance(spools_data, list):
        spools_list = spools_data
    else:
        return result

    spool_by_id = {s.get("id"): s for s in spools_list if isinstance(s, dict)}

    for slot, spool_id in slot_bindings.items():
        spool = spool_by_id.get(spool_id)
        if spool:
            result[str(slot)] = {
                "spool_id": spool_id,
                "remaining_weight": spool.get("remaining_weight", 0),
                "location": spool.get("location", ""),
            }
        else:
            result[str(slot)] = {"spool_id": spool_id, "remaining_weight": None, "location": ""}

    return result


# ── AppDaemon Log Fetch ──────────────────────────────────────────────

def _fetch_appdaemon_log(cfg: Config, lines: int = 100) -> list[str]:
    """Fetch last N lines of AppDaemon addon log via HA Supervisor API."""
    code, body = _ha_request(
        cfg,
        f"/api/hassio/addons/{cfg.appdaemon_addon_id}/logs",
        timeout=15.0,
    )
    if code != 200:
        log.warning("AppDaemon log fetch failed: HTTP %d", code)
        return [f"(log fetch failed: HTTP {code})"]
    # Logs come as plain text, one line per line
    all_lines = body.strip().splitlines()
    return all_lines[-lines:]


# ── State Persistence ────────────────────────────────────────────────

def _save_state(state: dict) -> None:
    """Persist monitor state to disk."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log.warning("State save failed: %s", e)


def _load_state() -> dict:
    """Load persisted monitor state, or return default."""
    try:
        if STATE_FILE.is_file():
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception as e:
        log.warning("State load failed, starting fresh: %s", e)
    return {"lifecycle_state": STATE_IDLE, "print_start": None, "job_name": None, "pre_weights": {}}


# ── Loop 1: HA Availability ─────────────────────────────────────────

class HAAvailabilityMonitor:
    """Polls HA /api/, detects outages, grabs AppDaemon logs on recovery."""

    def __init__(self, cfg: Config, shutdown_event: threading.Event):
        self.cfg = cfg
        self.shutdown = shutdown_event
        self.outage_start: str | None = None
        self.outage_start_ts: float = 0.0
        self.consecutive_fails = 0
        self.availability_log = _setup_artifact_log(cfg, "ha_availability", "ha_availability.log")

    def run(self) -> None:
        log.info("HA availability monitor started — polling %s every %ds", self.cfg.ha_url, self.cfg.ha_poll_interval)
        while not self.shutdown.is_set():
            try:
                self._poll()
            except Exception as e:
                log.error("HA availability poll error: %s", e)

            interval = self.cfg.outage_poll_interval if self.outage_start else self.cfg.ha_poll_interval
            self.shutdown.wait(interval)

    def _poll(self) -> None:
        t0 = time.monotonic()
        code, _ = _ha_request(self.cfg, "/api/", timeout=10.0)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if code == 200:
            if self.outage_start:
                # Recovery
                duration_s = time.time() - self.outage_start_ts
                duration_fmt = f"{int(duration_s)}s"
                if duration_s >= 60:
                    duration_fmt = f"{int(duration_s // 60)}m{int(duration_s % 60)}s"

                log.info("HA RECOVERY after %s (was down since %s)", duration_fmt, self.outage_start)
                self.availability_log.info("RECOVERY duration=%s start=%s", duration_fmt, self.outage_start)

                # Fetch AppDaemon log and write outage artifact
                appd_log = _fetch_appdaemon_log(self.cfg, lines=200)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                artifact = {
                    "start": self.outage_start,
                    "end": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "duration_s": round(duration_s, 1),
                    "appdaemon_log_tail": appd_log,
                }
                artifact_path = self.cfg.artifact_root / "ha_outages" / f"{ts}_outage.json"
                _write_artifact(artifact_path, artifact)

                self.outage_start = None
                self.outage_start_ts = 0.0
                self.consecutive_fails = 0
            else:
                self.availability_log.info("OK response_time=%dms", elapsed_ms)
        else:
            self.consecutive_fails += 1
            if not self.outage_start:
                self.outage_start = datetime.datetime.now(datetime.timezone.utc).isoformat()
                self.outage_start_ts = time.time()
                log.warning("HA OUTAGE DETECTED — HTTP %d at %s", code, self.outage_start)
                self.availability_log.warning("OUTAGE_START code=%d", code)
            else:
                log.info("HA STILL DOWN — HTTP %d (fail #%d, since %s)", code, self.consecutive_fails, self.outage_start)


# ── Loop 2: System Resources ────────────────────────────────────────

class SystemResourceMonitor:
    """Polls HA system monitor entities + local /proc for resource metrics."""

    def __init__(self, cfg: Config, shutdown_event: threading.Event):
        self.cfg = cfg
        self.shutdown = shutdown_event
        self.resource_log = _setup_artifact_log(cfg, "system_resources", "system_resources.log")
        self.last_alert_ts: dict[str, float] = {}
        # CPU sampling state (for /proc/stat delta)
        self._prev_cpu_idle: int = 0
        self._prev_cpu_total: int = 0
        self._has_prev_cpu: bool = False

    def run(self) -> None:
        log.info(
            "System resource monitor started — polling every %ds",
            self.cfg.resource_poll_interval,
        )
        while not self.shutdown.is_set():
            try:
                self._poll()
            except Exception as e:
                log.error("System resource poll error: %s", e)
            self.shutdown.wait(self.cfg.resource_poll_interval)

    def _poll(self) -> None:
        ha = self._poll_ha_resources()
        local = self._poll_local_resources()

        # Build log line
        parts = []
        for key in ("ha_cpu", "ha_mem", "ha_disk", "ha_swap"):
            val = ha.get(key)
            parts.append(f"{key}={val}%" if val is not None else f"{key}=n/a")
        val = ha.get("ha_temp")
        parts.append(f"ha_temp={val}\u00b0F" if val is not None else "ha_temp=n/a")
        for key in ("ska_cpu", "ska_mem", "ska_disk"):
            val = local.get(key)
            parts.append(f"{key}={val}%" if val is not None else f"{key}=n/a")
        self.resource_log.info(" ".join(parts))

        # Threshold checks
        self._check_threshold(ha.get("ha_cpu"), self.cfg.resource_cpu_warn, "CPU", "HA", "%")
        self._check_threshold(ha.get("ha_mem"), self.cfg.resource_mem_warn, "Memory", "HA", "%")
        self._check_threshold(ha.get("ha_disk"), self.cfg.resource_disk_warn, "Disk", "HA", "%")
        self._check_threshold(ha.get("ha_swap"), self.cfg.resource_swap_warn, "Swap", "HA", "%")
        self._check_threshold(ha.get("ha_temp"), self.cfg.resource_temp_warn_f, "CPU Temp", "HA", "\u00b0F")
        self._check_threshold(local.get("ska_cpu"), self.cfg.resource_cpu_warn, "CPU", "ska", "%")
        self._check_threshold(local.get("ska_mem"), self.cfg.resource_mem_warn, "Memory", "ska", "%")
        self._check_threshold(local.get("ska_disk"), self.cfg.resource_disk_warn, "Disk", "ska", "%")

    def _poll_ha_resources(self) -> dict[str, float | None]:
        """Read HA system monitor entities via REST API."""
        result: dict[str, float | None] = {}
        for key, entity_id in HA_RESOURCE_ENTITIES.items():
            state = _ha_get_state(self.cfg, entity_id)
            if state is None:
                result[key] = None
                continue
            raw = state.get("state", "")
            if raw in ("unknown", "unavailable", ""):
                result[key] = None
                continue
            try:
                result[key] = round(float(raw), 1)
            except (ValueError, TypeError):
                result[key] = None
        return result

    def _poll_local_resources(self) -> dict[str, float | None]:
        """Read CPU/mem/disk from local /proc and df."""
        result: dict[str, float | None] = {}
        result["ska_cpu"] = self._read_cpu()
        result["ska_mem"] = self._read_memory()
        result["ska_disk"] = self._read_disk()
        return result

    def _read_cpu(self) -> float | None:
        """Compute CPU% from /proc/stat delta between two polls."""
        try:
            with open("/proc/stat") as f:
                for line in f:
                    if line.startswith("cpu "):
                        fields = [int(x) for x in line.split()[1:]]
                        break
                else:
                    return None
        except (OSError, ValueError):
            return None

        # fields: user nice system idle iowait irq softirq steal [guest guest_nice]
        idle_total = fields[3] + fields[4]  # idle + iowait
        cpu_total = sum(fields)

        if not self._has_prev_cpu:
            self._prev_cpu_idle = idle_total
            self._prev_cpu_total = cpu_total
            self._has_prev_cpu = True
            return None  # First poll, no delta available

        delta_idle = idle_total - self._prev_cpu_idle
        delta_total = cpu_total - self._prev_cpu_total
        self._prev_cpu_idle = idle_total
        self._prev_cpu_total = cpu_total

        if delta_total <= 0:
            return None
        return round((1.0 - delta_idle / delta_total) * 100, 1)

    def _read_memory(self) -> float | None:
        """Read memory usage from /proc/meminfo."""
        try:
            mem_total = mem_available = None
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        mem_total = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        mem_available = int(line.split()[1])
                    if mem_total is not None and mem_available is not None:
                        break
            if mem_total and mem_available is not None:
                return round((mem_total - mem_available) / mem_total * 100, 1)
        except (OSError, ValueError):
            pass
        return None

    def _read_disk(self) -> float | None:
        """Read root disk usage via df."""
        try:
            result = subprocess.run(
                ["df", "--output=pcent", "/"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                if len(lines) >= 2:
                    return float(lines[1].strip().rstrip("%"))
        except Exception:
            pass
        return None

    def _check_threshold(
        self,
        value: float | None,
        threshold: float,
        label: str,
        host: str,
        unit: str,
    ) -> None:
        """Log and optionally notify on threshold breach."""
        if value is None or value < threshold:
            return

        metric_key = f"{host}_{label}"
        self.resource_log.warning(
            "THRESHOLD_BREACH host=%s metric=%s value=%s%s threshold=%s%s",
            host, label, value, unit, threshold, unit,
        )

        now = time.time()
        last = self.last_alert_ts.get(metric_key, 0.0)
        if now - last >= _ALERT_COOLDOWN_S:
            self.last_alert_ts[metric_key] = now
            _send_ha_notification(
                self.cfg,
                "\u26a0\ufe0f Resource Alert",
                f"{label} at {value}{unit} on {host} (threshold: {threshold}{unit})",
            )


# ── Loop 3: Print Lifecycle ──────────────────────────────────────────

class PrintLifecycleMonitor:
    """State machine: IDLE → PREPARING → PRINTING → FINISHING → IDLE."""

    def __init__(self, cfg: Config, shutdown_event: threading.Event):
        self.cfg = cfg
        self.shutdown = shutdown_event

        # Load persisted state
        saved = _load_state()
        self.state = saved.get("lifecycle_state", STATE_IDLE)
        self.print_start: str | None = saved.get("print_start")
        self.print_start_ts: float = 0.0
        self.job_name: str | None = saved.get("job_name")
        self.pre_weights: dict = saved.get("pre_weights", {})
        self.last_gcode_state = ""

        if self.state in (STATE_PRINTING, STATE_PREPARING):
            log.info("Resuming from persisted state: %s (job=%s)", self.state, self.job_name)
            self._needs_rehydration_check = True
        else:
            self._needs_rehydration_check = False

    def run(self) -> None:
        log.info("Print lifecycle monitor started — polling every %ds", self.cfg.print_poll_interval)

        # Wait for HA to be reachable before entering main loop
        while not self.shutdown.is_set():
            code, _ = _ha_request(self.cfg, "/api/", timeout=5.0)
            if code == 200:
                break
            log.info("Waiting for HA to be reachable...")
            self.shutdown.wait(5)

        while not self.shutdown.is_set():
            try:
                self._poll()
            except Exception as e:
                log.error("Print lifecycle poll error: %s", e)
            self.shutdown.wait(self.cfg.print_poll_interval)

    def _get_gcode_state(self) -> str:
        state = _ha_get_state(self.cfg, self.cfg.entities["print_status"])
        if state:
            return str(state.get("state", "")).strip().lower()
        return ""

    def _get_job_name(self) -> str:
        state = _ha_get_state(self.cfg, self.cfg.entities["task_name"])
        if state:
            return str(state.get("state", "")).strip()
        return ""

    def _get_progress(self) -> int:
        state = _ha_get_state(self.cfg, self.cfg.entities["print_progress"])
        if state:
            try:
                return int(float(state.get("state", "0")))
            except (ValueError, TypeError):
                pass
        return 0

    def _get_active_tray(self) -> int | None:
        state = _ha_get_state(self.cfg, self.cfg.entities["active_tray"])
        if state:
            try:
                val = int(state.get("state", "0"))
                return val if val > 0 else None
            except (ValueError, TypeError):
                pass
        return None

    def _persist_state(self) -> None:
        _save_state({
            "lifecycle_state": self.state,
            "print_start": self.print_start,
            "job_name": self.job_name,
            "pre_weights": self.pre_weights,
        })

    def _poll(self) -> None:
        # Rehydration check: verify persisted job matches current HA state
        if self._needs_rehydration_check:
            self._needs_rehydration_check = False
            current_job = self._get_job_name()
            if current_job and current_job != self.job_name:
                log.info(
                    "REHYDRATE_JOB_MISMATCH persisted=%s current=%s — refreshing state",
                    self.job_name, current_job,
                )
                self.job_name = current_job
                self.print_start = datetime.datetime.now(datetime.timezone.utc).isoformat()
                self.print_start_ts = time.time()
                self.pre_weights = _snapshot_spoolman_weights(self.cfg)
                log.info("REHYDRATE_PRE_WEIGHTS_REFRESHED slots=%d job=%s", len(self.pre_weights), self.job_name)
                self._persist_state()
            elif current_job:
                log.info("REHYDRATE_JOB_MATCH job=%s — continuing", self.job_name)

        gcode_state = self._get_gcode_state()
        if not gcode_state:
            return  # HA unreachable, skip

        prev_state = self.state

        if self.state == STATE_IDLE:
            if gcode_state == "prepare":
                self._transition_to_preparing(gcode_state)
            elif gcode_state == "running":
                # Missed prepare, jump to printing
                self._transition_to_preparing(gcode_state)
                self._transition_to_printing(gcode_state)

        elif self.state == STATE_PREPARING:
            if gcode_state == "running":
                self._transition_to_printing(gcode_state)
            elif gcode_state in TERMINAL_STATES:
                self._transition_to_finishing(gcode_state)
            elif gcode_state == "idle":
                # Cancelled during prepare
                log.info("Print cancelled during prepare, returning to idle")
                self.state = STATE_IDLE
                self._persist_state()

        elif self.state == STATE_PRINTING:
            if gcode_state in TERMINAL_STATES:
                self._transition_to_finishing(gcode_state)
            elif gcode_state == "idle":
                # Unexpected idle during print
                log.warning("Unexpected idle during print — treating as finish")
                self._transition_to_finishing("idle")
            else:
                # Still printing — log progress periodically
                progress = self._get_progress()
                if progress > 0 and progress % 10 == 0:
                    log.info("Print progress: %d%% (job=%s)", progress, self.job_name)

        elif self.state == STATE_FINISHING:
            # Finishing is handled inline, should not persist
            pass

        self.last_gcode_state = gcode_state

    def _transition_to_preparing(self, gcode_state: str) -> None:
        self.job_name = self._get_job_name() or "unknown"
        self.print_start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.print_start_ts = time.time()
        self.pre_weights = _snapshot_spoolman_weights(self.cfg)
        self.state = STATE_PREPARING
        log.info("PREPARING: job=%s pre_weights=%d slots", self.job_name, len(self.pre_weights))
        self._persist_state()

    def _transition_to_printing(self, gcode_state: str) -> None:
        self.state = STATE_PRINTING
        log.info("PRINTING: job=%s", self.job_name)
        self._persist_state()

    def _transition_to_finishing(self, gcode_state: str) -> None:
        self.state = STATE_FINISHING
        log.info("FINISHING: job=%s gcode_state=%s — waiting 30s for AppDaemon writes", self.job_name, gcode_state)

        # Wait for AppDaemon to process the print finish
        self.shutdown.wait(30)
        if self.shutdown.is_set():
            return

        # Post-print snapshot
        post_weights = _snapshot_spoolman_weights(self.cfg)
        print_end = datetime.datetime.now(datetime.timezone.utc).isoformat()
        duration_s = time.time() - self.print_start_ts if self.print_start_ts > 0 else 0

        # Weight deltas
        weight_delta = {}
        for slot in set(list(self.pre_weights.keys()) + list(post_weights.keys())):
            pre_w = self.pre_weights.get(slot, {}).get("remaining_weight")
            post_w = post_weights.get(slot, {}).get("remaining_weight")
            if pre_w is not None and post_w is not None:
                delta = round(pre_w - post_w, 2)
                if abs(delta) > 0.01:
                    weight_delta[slot] = delta

        # Active tray
        active_tray = self._get_active_tray()
        active_spool_id = None
        if active_tray and str(active_tray) in post_weights:
            active_spool_id = post_weights[str(active_tray)].get("spool_id")

        # AppDaemon log
        appd_log = _fetch_appdaemon_log(self.cfg, lines=100)

        # Build artifact
        artifact = {
            "job_name": self.job_name,
            "gcode_state": gcode_state,
            "print_start": self.print_start,
            "print_end": print_end,
            "duration_s": round(duration_s, 1),
            "active_tray": active_tray,
            "active_spool_id": active_spool_id,
            "pre_weights": self.pre_weights,
            "post_weights": post_weights,
            "weight_delta": weight_delta,
            "appdaemon_log_tail": appd_log,
        }

        # Safe filename
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (self.job_name or "unknown"))[:80]
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{ts}_{safe_name}"

        artifact_path = self.cfg.artifact_root / "prints" / f"{base_name}.json"
        _write_artifact(artifact_path, artifact)

        # Human-readable summary
        summary_lines = [
            f"Print Job: {self.job_name}",
            f"Status: {gcode_state}",
            f"Start: {self.print_start}",
            f"End: {print_end}",
            f"Duration: {int(duration_s)}s ({int(duration_s // 60)}m{int(duration_s % 60)}s)",
            "",
            "Weight Changes:",
        ]
        if weight_delta:
            for slot, delta in sorted(weight_delta.items()):
                pre_w = self.pre_weights.get(slot, {}).get("remaining_weight", "?")
                post_w = post_weights.get(slot, {}).get("remaining_weight", "?")
                spool_id = post_weights.get(slot, {}).get("spool_id", "?")
                summary_lines.append(f"  Slot {slot} (spool #{spool_id}): {pre_w}g -> {post_w}g (consumed {delta}g)")
        else:
            summary_lines.append("  (no weight changes detected)")

        summary_lines.extend(["", f"AppDaemon log: {len(appd_log)} lines captured"])
        summary_path = self.cfg.artifact_root / "prints" / f"{base_name}.txt"
        _write_text_artifact(summary_path, "\n".join(summary_lines) + "\n")

        log.info(
            "PRINT COMPLETE: job=%s status=%s duration=%ds deltas=%s",
            self.job_name, gcode_state, int(duration_s), weight_delta or "(none)",
        )

        # Reset to idle
        self.state = STATE_IDLE
        self.print_start = None
        self.print_start_ts = 0.0
        self.job_name = None
        self.pre_weights = {}
        self._persist_state()


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    # Configure logging to stdout (systemd journald captures it)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )

    log.info("Filament IQ Monitor starting")

    # Validate config files exist
    if not SECRETS_FILE.is_file():
        log.error("Secrets file not found: %s", SECRETS_FILE)
        sys.exit(1)
    if not CONFIG_FILE.is_file():
        log.error("Config file not found: %s", CONFIG_FILE)
        sys.exit(1)

    cfg = Config()
    log.info(
        "Config loaded — HA: %s, Spoolman: %s, Artifacts: %s, Printer: %s, Slots: %s",
        cfg.ha_url, cfg.spoolman_url, cfg.artifact_root, cfg.printer_serial, cfg.ams_slots,
    )

    # Ensure artifact directories exist
    try:
        (cfg.artifact_root / "ha_outages").mkdir(parents=True, exist_ok=True)
        (cfg.artifact_root / "prints").mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning("Could not create artifact dirs (NAS unavailable?): %s", e)

    # Shutdown coordination
    shutdown_event = threading.Event()

    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        log.info("Received %s — shutting down gracefully", sig_name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Wait for HA to be reachable before starting loops
    log.info("Waiting for HA to be reachable at %s ...", cfg.ha_url)
    while not shutdown_event.is_set():
        code, _ = _ha_request(cfg, "/api/", timeout=5.0)
        if code == 200:
            log.info("HA reachable (HTTP 200)")
            break
        log.info("HA not yet reachable (HTTP %d), retrying in 10s...", code)
        shutdown_event.wait(10)

    if shutdown_event.is_set():
        log.info("Shutdown before HA became reachable")
        return

    # Start monitor threads
    ha_monitor = HAAvailabilityMonitor(cfg, shutdown_event)
    resource_monitor = SystemResourceMonitor(cfg, shutdown_event)
    print_monitor = PrintLifecycleMonitor(cfg, shutdown_event)

    ha_thread = threading.Thread(target=ha_monitor.run, name="ha-availability", daemon=True)
    resource_thread = threading.Thread(target=resource_monitor.run, name="system-resources", daemon=True)
    print_thread = threading.Thread(target=print_monitor.run, name="print-lifecycle", daemon=True)

    ha_thread.start()
    resource_thread.start()
    print_thread.start()

    log.info("Monitor running — 3 threads active")

    # Main thread waits for shutdown signal
    while not shutdown_event.is_set():
        shutdown_event.wait(1.0)

    # Wait for threads to finish current work
    log.info("Waiting for threads to finish...")
    ha_thread.join(timeout=15)
    resource_thread.join(timeout=15)
    print_thread.join(timeout=15)

    log.info("Filament IQ Monitor stopped")


if __name__ == "__main__":
    main()
