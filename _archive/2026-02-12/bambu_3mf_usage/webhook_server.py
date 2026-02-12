#!/usr/bin/env python3
"""
Minimal webhook server for 3MF after-print. Run on a machine with Python (e.g. your Mac).
On POST /run, runs run_after_print.py. Use when HA host has no Python.
Usage: python3 webhook_server.py [--port 8765] [--bind 0.0.0.0]
Then set input_text.bambu_3mf_webhook_url in HA to http://<this-machine-ip>:8765
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RUN_SCRIPT = SCRIPT_DIR / "run_after_print.py"
CONFIG = SCRIPT_DIR / "config.json"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") == "/run":
            self.run_3mf()
        else:
            self.send_error(404)

    def run_3mf(self):
        if not RUN_SCRIPT.exists():
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": false, "error": "run_after_print.py not found"}')
            return
        try:
            proc = subprocess.run(
                [sys.executable, str(RUN_SCRIPT)],
                cwd=str(SCRIPT_DIR),
                capture_output=True,
                text=True,
                timeout=120,
            )
            body = {"ok": proc.returncode == 0, "returncode": proc.returncode}
            if proc.stderr:
                body["stderr"] = proc.stderr[-500:]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())
        except subprocess.TimeoutExpired:
            self.send_response(504)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": false, "error": "timeout"}')
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())

    def log_message(self, format, *args):
        print(f"[webhook] {args[0]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bind", default="0.0.0.0")
    args = ap.parse_args()
    if not CONFIG.exists():
        print(f"Warning: {CONFIG} not found. Create from config.example.json (ha_url, ha_token, printer_ip, access_code).")
    print(f"Listening on http://{args.bind}:{args.port} – POST /run to trigger 3MF flow.")
    server = HTTPServer((args.bind, args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
