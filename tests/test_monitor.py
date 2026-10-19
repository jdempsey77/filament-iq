"""Tests for monitor/monitor.py (the standalone ska companion daemon)."""

import importlib.util
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

_MONITOR_PATH = Path(__file__).resolve().parent.parent / "monitor" / "monitor.py"


def _load_monitor(name="fiq_monitor_under_test"):
    spec = importlib.util.spec_from_file_location(name, _MONITOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def monitor():
    return _load_monitor()


# ── SECRETS_FILE override ────────────────────────────────────────────

def test_secrets_file_defaults_to_config_dir(monkeypatch):
    monkeypatch.delenv("FILAMENT_IQ_SECRETS_FILE", raising=False)
    mod = _load_monitor("fiq_monitor_default")
    assert mod.SECRETS_FILE == mod.CONFIG_DIR / "secrets.env"


def test_secrets_file_env_override(monkeypatch, tmp_path):
    target = tmp_path / "fifo-from-sops"
    monkeypatch.setenv("FILAMENT_IQ_SECRETS_FILE", str(target))
    mod = _load_monitor("fiq_monitor_override")
    assert mod.SECRETS_FILE == target


def test_config_file_is_conf(monitor):
    assert monitor.CONFIG_FILE.name == "monitor-config.conf"


def test_spoolman_default_is_placeholder_not_real_ip():
    src = _MONITOR_PATH.read_text()
    assert "192.168." not in src
    assert '"SPOOLMAN_URL", "http://YOUR_HA_IP:7912"' in src


# ── _load_env_file ───────────────────────────────────────────────────

def test_load_env_file_regular_file(monitor, tmp_path):
    p = tmp_path / "x.env"
    p.write_text("# c\nA=1\nB='two'\n\nnoeq\n")
    assert monitor._load_env_file(p) == {"A": "1", "B": "two"}


def test_load_env_file_missing_returns_empty(monitor, tmp_path):
    assert monitor._load_env_file(tmp_path / "nope") == {}


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_load_env_file_reads_fifo(monitor, tmp_path):
    """sops exec-file hands over a FIFO; is_file() is False for it."""
    fifo = tmp_path / "secrets.fifo"
    os.mkfifo(fifo)
    assert not fifo.is_file()

    def writer():
        with open(fifo, "w") as f:
            f.write('HOME_ASSISTANT_TOKEN="tok"\n')

    t = threading.Thread(target=writer)
    t.start()
    try:
        result = monitor._load_env_file(fifo)
    finally:
        t.join(timeout=5)
    assert result == {"HOME_ASSISTANT_TOKEN": "tok"}


# ── resource_alert_title ─────────────────────────────────────────────

def test_resource_alert_title_short(monitor):
    t = monitor.resource_alert_title("ska", "CPU", 91, "%")
    assert t == "\U0001F7E0 ska CPU 91%"


def test_resource_alert_title_truncated_to_40(monitor):
    t = monitor.resource_alert_title("a-very-long-hostname-indeed", "Memory usage", 99.5, "%")
    assert len(t) <= 40
    assert t.endswith("…")


# ── _send_ha_notification payload ────────────────────────────────────

class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"[]"


@pytest.fixture
def captured(monitor, monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, json.loads(req.data.decode())))
        return _Resp()

    monkeypatch.setattr(monitor.urllib.request, "urlopen", fake_urlopen)
    return calls


def _cfg():
    return SimpleNamespace(ha_url="http://ha.invalid:8123", ha_token="t")


def test_send_notification_calls_notify_jerry(monitor, captured):
    monitor._send_ha_notification(_cfg(), "T", "M", "res-x", "ops_res_x", bell_message="long")
    url, fields = captured[0]
    assert url.endswith("/api/services/script/notify_jerry")
    assert fields["title"] == "T" and fields["tag"] == "res-x"
    assert fields["group"] == monitor.NOTIFY_GROUP
    assert fields["url"] == monitor.NOTIFY_URL
    assert fields["bell_message"] == "long"


def test_send_notification_clear_payload(monitor, captured):
    monitor._send_ha_notification(_cfg(), "", "", "res-x", "ops_res_x", clear=True, bell_message="x")
    _, fields = captured[0]
    assert fields == {"tag": "res-x", "persist": "ops_res_x", "clear": True}


# ── clear-on-recover ─────────────────────────────────────────────────

def test_clear_on_recover_only_once(monitor, monkeypatch):
    sent = []
    monkeypatch.setattr(
        monitor, "_send_ha_notification",
        lambda cfg, title, message, tag, persist="", clear=False, bell_message="":
            sent.append({"title": title, "tag": tag, "persist": persist, "clear": clear}),
    )
    mon = monitor.SystemResourceMonitor.__new__(monitor.SystemResourceMonitor)
    mon.cfg = _cfg()
    mon.resource_log = SimpleNamespace(warning=lambda *a, **k: None)
    mon.last_alert_ts = {}
    mon.alerting = set()

    # Under threshold with no prior alert: nothing sent.
    mon._check_threshold(10.0, 80.0, "CPU", "ska", "%")
    assert sent == []

    mon._check_threshold(91.0, 80.0, "CPU", "ska", "%")
    assert len(sent) == 1 and not sent[0]["clear"]
    assert sent[0]["tag"] == "res-ska-cpu"
    assert sent[0]["persist"] == "ops_res_ska_cpu"
    assert len(sent[0]["title"]) <= 40

    mon._check_threshold(50.0, 80.0, "CPU", "ska", "%")
    mon._check_threshold(40.0, 80.0, "CPU", "ska", "%")
    clears = [s for s in sent if s["clear"]]
    assert len(clears) == 1
    assert clears[0]["tag"] == "res-ska-cpu"
    assert "ska_CPU" not in mon.alerting
