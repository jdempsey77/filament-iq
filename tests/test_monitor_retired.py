"""The ska monitor moved to print-ops (its single source). monitor/deploy.sh must refuse and
never copy or restart anything; no second monitor.py may come back here."""
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "monitor" / "deploy.sh"


def test_no_second_monitor_copy():
    assert not (ROOT / "monitor" / "monitor.py").exists()
    assert not (ROOT / "monitor" / "filament-iq-monitor.service").exists()


def test_deploy_stub_refuses_and_touches_nothing(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("ssh", "scp"):
        p = bin_dir / name
        p.write_text(f'#!/bin/bash\necho "{name} $*" >> "{log}"\n')
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", MONITOR_HOST="ska")
    r = subprocess.run(["bash", str(DEPLOY)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode != 0
    assert "print-ops" in r.stderr
    assert not log.exists()
