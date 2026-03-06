#!/usr/bin/env python3
import re, sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
APP_DIR = REPO_ROOT / "appdaemon" / "apps" / "filament_iq"
ALLOWED_PORTS = {80, 443, 22, 990, 7912}
ENTITY_DOMAINS = ("input_boolean.", "input_text.", "input_button.", "sensor.",
                  "input_number.", "input_select.", "input_datetime.")
findings = []

def report(filepath, lineno, kind, detail):
    findings.append(f"  {kind:18s}  {filepath.name}:{lineno}  {detail}")

def audit_file(filepath):
    with open(filepath) as f:
        lines = f.readlines()
    in_docstring = False; docstring_char = None
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        for tq in ('"""', "'''"):
            if tq in line:
                count = line.count(tq)
                if not in_docstring and count % 2 == 1:
                    in_docstring = True; docstring_char = tq; break
                elif in_docstring and tq == docstring_char:
                    in_docstring = False; break
        if in_docstring or stripped.startswith("#"): continue

        for m in re.finditer(r'\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b', line):
            octets = [int(x) for x in m.groups()]
            if octets[0] == 192 and octets[1] == 0 and octets[2] == 2: continue
            if "YOUR_" in line: continue
            report(filepath, i, "HARDCODED_IP", f'"{m.group()}"')

        for domain in ENTITY_DOMAINS:
            for m in re.finditer(re.escape(domain) + r'[\w_]+', line):
                entity_id = m.group()
                if "self.args" in line or "YOUR_" in entity_id: continue
                if ".filament_iq_" in entity_id or ".ams_slot_" in entity_id: continue
                if "test_" in str(filepath).lower(): continue
                report(filepath, i, "HARDCODED_ENTITY", f'"{entity_id}"')

        for m in re.finditer(r'\b(\d{4,5})\b', line):
            port = int(m.group())
            if port in ALLOWED_PORTS or port > 65535 or 2000 <= port <= 2030 or port == 1000: continue
            if "YOUR_" in line: continue
            report(filepath, i, "HARDCODED_PORT", f'port {port}')

        for m in re.finditer(r'["\']([a-zA-Z0-9]{16,})["\']', line):
            val = m.group(1)
            if not (re.search(r'[a-zA-Z]', val) and re.search(r'[0-9]', val)): continue
            if any(val.startswith(p) for p in ("filament_iq", "input_", "sensor")): continue
            if "YOUR_" in val or re.fullmatch(r'[a-fA-F0-9]+', val): continue
            report(filepath, i, "POSSIBLE_SERIAL", f'"{val}"')

py_files = sorted(APP_DIR.glob("*.py"))
print(f"Auditing {len(py_files)} files in {APP_DIR.relative_to(REPO_ROOT)}")
for f in py_files:
    audit_file(f)
print()
if findings:
    print(f"ISSUES FOUND: {len(findings)}")
    for f in findings: print(f)
    sys.exit(1)
else:
    print("CLEAN -- no hardcoded instance-specific values found")
    sys.exit(0)
