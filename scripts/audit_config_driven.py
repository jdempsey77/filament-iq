#!/usr/bin/env python3
"""FilamentIQ config-driven audit — find hardcoded instance-specific values."""

import os
import re
import sys


ENTITY_DOMAIN_RE = re.compile(
    r"(input_boolean|input_text|input_button|sensor|input_number|input_select|"
    r"input_datetime)\.[a-zA-Z0-9_]+"
)
IP_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")
# Only match explicit port= or port: (avoid "255" in color_distance_threshold, etc.)
PORT_RE = re.compile(r"\b(?:port|PORT)\s*[=:]\s*(\d{3,5})\b")
STANDARD_PORTS = {"80", "443", "22", "990"}  # 990 = FTPS
SERIAL_RE = re.compile(r"\b[a-zA-Z0-9]{16,}\b")
SERIAL_EXCLUDE = re.compile(
    r"^(LOCATION_|STATUS_|UNBOUND_|AMS\d|NEXT_|FULL_|RFID_|NONRFID_|"
    r"BAMBU_|PLACEHOLDER|EVENT_|SEEN_|STATUS_OK|STATUS_|"
    r"[0-9a-f]{32}$|^[0-9]+$)",
    re.I,
)


def is_in_comment_or_docstring(line: str, in_docstring: bool) -> tuple[bool, bool]:
    stripped = line.strip()
    if stripped.startswith("#"):
        return True, in_docstring
    if '"""' in line or "'''" in line:
        return True, not in_docstring
    if in_docstring:
        return True, True
    return False, in_docstring


def is_args_default(lines: list[str], line_idx: int, match_start: int, match_end: int) -> bool:
    """True if entity is the default (second arg) in .get("key", "default")."""
    line = lines[line_idx]
    before = line[:match_start]
    # Same line: .get("key", "entity_id")
    if re.search(r"\.get\s*\([^)]*,\s*[\"']\s*$", before):
        return True
    if re.search(r"args\.get\s*\([^)]*,\s*[\"']\s*$", before):
        return True
    # Same line: "key", "entity_id" (second arg of .get on previous line)
    if re.search(r'["\']\s*,\s*["\']\s*$', before):
        for j in range(line_idx - 1, max(-1, line_idx - 5), -1):
            if ".get" in lines[j] and "(" in lines[j]:
                return True
            if lines[j].strip().startswith(")"):
                break
    # Also: before ends with comma+quote (continuation of .get second arg)
    if re.search(r",\s*[\"']\s*$", before):
        for j in range(line_idx - 1, max(-1, line_idx - 5), -1):
            if ".get" in lines[j] and "(" in lines[j]:
                return True
            if lines[j].strip().startswith(")"):
                break
    # Multi-line: entity on new line after "key", — before is just whitespace + quote
    if re.match(r"^\s+[\"']\s*$", before) or (len(before) < 30 and re.match(r"^\s*[\"']", before)):
        for j in range(line_idx - 1, max(-1, line_idx - 5), -1):
            prev = lines[j]
            if ".get" in prev and "(" in prev:
                return True
            if re.search(r",\s*$", prev):  # previous line was "key",
                return True
            if prev.strip().startswith(")"):
                break
    return False


def is_format_or_fstring(line: str, match_start: int) -> bool:
    before = line[:match_start]
    if "{" in before and ("f\"" in before or "f'" in before):
        return True
    if ".format(" in line:
        return True
    return False


def is_docstring_value(line: str) -> bool:
    """Check if line is inside a docstring (triple-quoted)."""
    return '"""' in line or "'''" in line


def scan_file(filepath: str) -> list[tuple[str, int, str]]:
    findings = []
    with open(filepath, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    in_docstring = False
    for i, line in enumerate(lines):
        line_no = i + 1
        skip, in_docstring = is_in_comment_or_docstring(line, in_docstring)
        if skip:
            continue

        # 1. IP addresses
        for m in IP_RE.finditer(line):
            ip = m.group(1)
            try:
                if all(0 <= int(p) <= 255 for p in ip.split(".")):
                    findings.append(("HARDCODED", line_no, f"IP address: {ip}"))
            except ValueError:
                pass

        # 2. Entity IDs not from self.args
        for m in ENTITY_DOMAIN_RE.finditer(line):
            ent = m.group(0)
            if ent.endswith("_"):  # fragment/prefix like "input_text.ams_slot_" or "sensor.spoolman_spool_"
                continue
            if is_args_default(lines, i, m.start(), m.end()):
                continue
            if is_format_or_fstring(line, m.start()):
                continue
            findings.append(("HARDCODED", line_no, f'entity ID: "{ent}"'))

        # 3. Non-standard port numbers
        for m in PORT_RE.finditer(line):
            port = m.group(1)
            if port not in STANDARD_PORTS and port.isdigit():
                findings.append(("HARDCODED", line_no, f"port: {port}"))

        # 4. Serial number pattern
        for m in SERIAL_RE.finditer(line):
            val = m.group(0)
            if SERIAL_EXCLUDE.match(val):
                continue
            if val.isdigit() and len(val) > 10:
                continue
            if re.match(r"^[0-9a-fA-F]+$", val) and len(val) >= 16:
                findings.append(("HARDCODED", line_no, f'serial-like: "{val}"'))

    return findings


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: audit_config_driven.py <filament_iq_dir>", file=sys.stderr)
        return 1
    root = sys.argv[1]
    if not os.path.isdir(root):
        print(f"Error: {root} is not a directory", file=sys.stderr)
        return 1
    total = 0
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        for kind, line_no, msg in scan_file(path):
            print(f'  HARDCODED  {name}:{line_no}  "{msg}"')
            total += 1
    print("")
    if total == 0:
        print("CLEAN")
        return 0
    print(f"ISSUES FOUND: {total}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
