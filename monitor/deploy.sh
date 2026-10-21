#!/bin/bash
# RETIRED 2026-10-20. The Filament IQ Monitor's single source is the print-ops repo
# (jdempsey77/print-ops, monitor.py). ska's filament-iq-monitor.service runs it from the
# ~/code/print-ops checkout, and /deploy deploys it (print-ops release.yaml, how: script,
# scripts/deploy-on-ska.sh). This script used to scp a second copy of monitor.py (with a
# placeholder SPOOLMAN_URL) over the live one; it now refuses and changes nothing.
echo "monitor/deploy.sh is retired: the monitor lives in the print-ops repo (monitor.py)." >&2
echo "Deploy it with /deploy (print-ops release.yaml, how: script). Nothing was copied or restarted." >&2
exit 1
