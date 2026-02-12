#!/usr/bin/env python3
"""
Wrapper for Home Assistant: fetch state from HA API, run bambu_3mf_usage CLI,
then either call spoolman.use_spool_filament + reload, or fire event bambu_3mf_no_match for fallback.
Run from HA shell_command with TASK_NAME and config path. Requires: requests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("run_after_print.py requires: pip install requests", file=sys.stderr)
    sys.exit(2)

# Same dir as this script
SCRIPT_DIR = Path(__file__).resolve().parent
# Entity IDs (P1S)
TASK_NAME_ENTITY = "sensor.p1s_01p00c5a3101668_task_name"
TRAYS_USED_ENTITY = "input_text.p1s_trays_used_this_print"
PRINT_WEIGHT_ENTITY = "sensor.p1s_01p00c5a3101668_print_weight"
LAST_TRAY_ENTITY = "input_text.p1s_last_tray_entity"
SLOT_SPOOL_ENTITY = "input_text.ams_slot_{}_spool_id"
SPOOLMAN_SPOOL_ENTITY = "sensor.spoolman_spool_{}"


def load_config(config_path: Path) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_states(ha_url: str, token: str) -> dict:
    """GET /api/states and return dict entity_id -> state object."""
    url = f"{ha_url.rstrip('/')}/api/states"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return {s["entity_id"]: s for s in r.json()}


def build_ams_and_spool(states: dict) -> tuple[dict, dict]:
    ams_state: dict = {}
    spool_map: dict = {}
    for slot in range(1, 7):
        e = states.get(SLOT_SPOOL_ENTITY.format(slot))
        spool_id = (e.get("state") or "").strip() if e else ""
        if not spool_id or spool_id == "0":
            continue
        try:
            sid = int(spool_id)
        except ValueError:
            continue
        spool_map[str(slot)] = sid
        # Color and material from Spoolman spool entity
        spool_e = states.get(SPOOLMAN_SPOOL_ENTITY.format(sid))
        attrs = (spool_e.get("attributes") or {}) if spool_e else {}
        color = (
            attrs.get("filament_color_hex")
            or attrs.get("color_hex")
            or attrs.get("color")
            or ""
        )
        if isinstance(color, str):
            color = color.strip().lstrip("#").lower()
        material = (attrs.get("filament_type") or attrs.get("material") or attrs.get("name") or "")
        if isinstance(material, str) and " " in material:
            # name might be "Bambu PLA Basic"; take first word as material hint if needed
            material = material.strip()
        ams_state[str(slot)] = {"color_hex": color or "", "material": material or ""}
    return ams_state, spool_map


def main() -> int:
    config_path = Path(os.environ.get("BAMBU_3MF_CONFIG", SCRIPT_DIR / "config.json"))
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1
    config = load_config(config_path)
    ha_url = (config.get("ha_url") or os.environ.get("HA_URL") or "http://localhost:8123").rstrip("/")
    token = config.get("ha_token") or os.environ.get("HA_TOKEN")
    if not token:
        print("ha_token or HA_TOKEN required", file=sys.stderr)
        return 1
    printer_ip = config.get("printer_ip") or os.environ.get("BAMBU_PRINTER_IP")
    access_code = config.get("access_code") or os.environ.get("BAMBU_ACCESS_CODE")
    if not printer_ip or not access_code:
        print("printer_ip and access_code required in config or env", file=sys.stderr)
        return 1
    task_name = os.environ.get("TASK_NAME", "").strip()
    if not task_name:
        states = get_states(ha_url, token)
        te = states.get(TASK_NAME_ENTITY)
        task_name = (te.get("state") or "").strip() if te else ""
    if not task_name:
        print("TASK_NAME empty and task_name entity not found", file=sys.stderr)
        return 1

    states = get_states(ha_url, token)
    ams_state, spool_map = build_ams_and_spool(states)
    if not spool_map:
        print("No AMS slots with spool_id", file=sys.stderr)
        fire_no_match(ha_url, token, states)
        return 0

    work_dir = SCRIPT_DIR
    ams_json = work_dir / "ams_state.json"
    spool_json = work_dir / "spool_map.json"
    result_json = work_dir / "result.json"
    with open(ams_json, "w", encoding="utf-8") as f:
        json.dump(ams_state, f)
    with open(spool_json, "w", encoding="utf-8") as f:
        json.dump(spool_map, f)

    cli = [
        sys.executable,
        str(SCRIPT_DIR / "bambu_3mf_usage.py"),
        "--printer-ip", printer_ip,
        "--access-code", access_code,
        "--task-name", task_name,
        "--ams-json", str(ams_json),
        "--spoolmap-json", str(spool_json),
        "--out", str(result_json),
        "--download-dir", str(work_dir),
    ]
    try:
        subprocess.run(cli, check=True, capture_output=True, timeout=120, cwd=str(SCRIPT_DIR))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        fire_no_match(ha_url, token, states)
        return 0

    if not result_json.exists():
        fire_no_match(ha_url, token, states)
        return 0
    with open(result_json, "r", encoding="utf-8") as f:
        result = json.load(f)
    matches = result.get("matches") or []
    if not matches:
        fire_no_match(ha_url, token, states)
        return 0

    # Call spoolman.use_spool_filament for each match
    for m in matches:
        spool_id = m.get("spool_id")
        used_g = m.get("used_g")
        if spool_id is None or used_g is None:
            continue
        r = requests.post(
            f"{ha_url}/api/services/spoolman/use_spool_filament",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"id": int(spool_id), "use_weight": float(used_g)},
            timeout=10,
        )
        if not r.ok:
            print(f"use_spool_filament failed: {r.status_code} {r.text}", file=sys.stderr)

    # Reload Spoolman integration
    r = requests.post(
        f"{ha_url}/api/services/script/turn_on",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"entity_id": "script.reload_spoolman_integration"},
        timeout=10,
    )
    if not r.ok:
        print(f"reload_spoolman failed: {r.status_code}", file=sys.stderr)
    return 0


def fire_no_match(ha_url: str, token: str, states: dict) -> None:
    """Fire event bambu_3mf_no_match so HA can run fallback (single-tray or notify)."""
    def get_state(entity_id: str) -> str:
        s = states.get(entity_id)
        return (s.get("state") or "").strip() if s else ""

    payload = {
        "job_name": get_state(TASK_NAME_ENTITY),
        "trays_used": get_state(TRAYS_USED_ENTITY),
        "filament_used_g": get_state(PRINT_WEIGHT_ENTITY),
        "tray_entity": get_state(LAST_TRAY_ENTITY),
    }
    try:
        requests.post(
            f"{ha_url}/api/events/bambu_3mf_no_match",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
    except Exception as e:
        print(f"Fire event failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
