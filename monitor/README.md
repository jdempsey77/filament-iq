# Filament IQ Monitor: moved

The companion daemon (HA availability, print lifecycle, system resources, Niimbot queue, D11
heartbeat) now lives in the **print-ops** repo (`monitor.py`), its single source. On ska,
`filament-iq-monitor.service` runs it from the `~/code/print-ops` checkout and `/deploy`
deploys it (print-ops `release.yaml`, how: script). Its unit file, deploy script, tests and
health check are there too.

What's left here:

- `deploy.sh`: retired. It refuses and points at print-ops; it no longer copies anything.
- `monitor-config.env.example`: the config keys the monitor (and `scripts/print_niimbot.sh`)
  read from `~/.config/filament_iq/monitor-config.conf`. Placeholder values only.
