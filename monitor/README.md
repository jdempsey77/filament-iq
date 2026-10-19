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
2. Copy `monitor-config.env.example` to `~/.config/filament_iq/monitor-config.conf`
   and edit values (`HA_URL`, `PRINTER_SERIAL` and `SPOOLMAN_URL` must be set;
   the `SPOOLMAN_URL` code default is only a placeholder)
3. Create `~/.config/filament_iq/secrets.env`:
   ```
   HOME_ASSISTANT_TOKEN=<your_token>
   ```
   The path can be overridden with the `FILAMENT_IQ_SECRETS_FILE` environment
   variable, e.g. a `sops exec-file` FIFO:
   `sops exec-file secrets.enc.env 'env FILAMENT_IQ_SECRETS_FILE={} python3 ~/filament_iq/monitor.py'`
4. Copy `filament-iq-monitor.service` to `~/.config/systemd/user/`
5. Enable: `systemctl --user enable --now filament-iq-monitor`

## Alerts
Resource threshold breaches call HA's `script.notify_jerry` (title
`<emoji> <host> <metric> <value>`, at most 40 chars; tag `res-<host>-<metric>`).
When the metric drops back under its threshold the push and bell entry are
cleared once.

## Deploy updates
```bash
MONITOR_HOST=ska bash monitor/deploy.sh
```
