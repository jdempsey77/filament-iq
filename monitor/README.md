# Filament IQ Monitor

Optional companion daemon that runs on a separate machine. Monitors HA
availability and print lifecycle, writing artifacts to a NAS share.

## What it does
- **HA availability loop** — polls HA every 30s, detects outages, captures
  AppDaemon logs on recovery
- **Print lifecycle loop** — state machine IDLE→PREPARING→PRINTING→FINISHING→IDLE,
  captures pre/post Spoolman weight snapshots and writes print artifacts

## Requirements
- A separate Linux machine with network access to HA and Spoolman
- Python 3.10+ (stdlib only, no pip dependencies)
- Optional: NAS mount at `ARTIFACT_ROOT`

## Setup
1. Copy `monitor.py` to `~/filament_iq/monitor.py` on the target machine
2. Copy `monitor-config.env.example` to `~/.config/filament_iq/monitor-config.env`
   and edit values
3. Create `~/.config/filament_iq/secrets.env`:
   ```
   HOME_ASSISTANT_TOKEN=<your_token>
   ```
4. Copy `filament-iq-monitor.service` to `~/.config/systemd/user/`
5. Enable: `systemctl --user enable --now filament-iq-monitor`

## Deploy updates
```bash
MONITOR_HOST=ska bash monitor/deploy.sh
```
