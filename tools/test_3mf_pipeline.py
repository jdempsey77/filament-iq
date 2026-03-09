#!/usr/bin/env python3
"""
3MF Pipeline Diagnostic — end-to-end validation of FTPS fetch, match, download, and parse.

Run from HA SSH:
  python3 /addon_configs/a0d7b954_appdaemon/apps/tools/test_3mf_pipeline.py

Run from dev machine:
  python3 tools/test_3mf_pipeline.py [--task "My Print Name"]

Requires: curl, network access to printer (192.168.4.114:990) and HA API.
"""

import argparse
import json
import os
import sys
import tempfile
import urllib.request
import zipfile

# ---------------------------------------------------------------------------
# Import from filament_iq.threemf_parser if possible, else fall back to curl
# ---------------------------------------------------------------------------

PARSER_IMPORTED = False

# Add candidate paths for filament_iq package:
# - On HA: script is at apps/tools/test_3mf_pipeline.py → parent is apps/
# - On dev: script is at tools/test_3mf_pipeline.py → try repo/appdaemon/apps/
_script_dir = os.path.dirname(os.path.abspath(__file__))
_candidates = [
    os.path.dirname(_script_dir),  # parent of tools/ (works on HA)
    os.path.join(os.path.dirname(_script_dir), "appdaemon", "apps"),  # repo layout
]
for p in _candidates:
    if p not in sys.path and os.path.isdir(os.path.join(p, "filament_iq")):
        sys.path.insert(0, p)

try:
    from filament_iq.threemf_parser import (
        ftps_connect,
        ftps_download_native,
        ftps_list_cache_native,
        ftps_list_dir,
        ftps_list_cache,
        ftps_download_3mf,
        find_best_3mf,
        normalize_task_name,
        parse_3mf_filaments,
    )
    PARSER_IMPORTED = True
    NATIVE_AVAILABLE = True
except ImportError:
    NATIVE_AVAILABLE = False
    pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PRINTER_IP = "192.168.4.114"
PRINTER_PORT = 990
SEARCH_DIRS = ["/cache", "/model", "/sdcard"]

ACCESS_CODE_ENTITY = "input_text.filament_iq_printer_access_code"
TASK_NAME_ENTITY = "sensor.p1s_01p00c5a3101668_task_name"

UNICODE_TEST_CASES = [
    "●● 4x6 Double Height Drawer Set",
    "Für Original Google Pixel Snap Charger",
    "V2 – new variant, slightly thicker walls",
    "5x4x9U Box",
]

# HA connection — try Supervisor token first (SSH on HA), then deploy.env.local
HA_URL = os.environ.get("HA_URL", "")
HA_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "") or os.environ.get("HA_TOKEN", "")

if not HA_URL or not HA_TOKEN:
    _env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "deploy.env.local",
    )
    if os.path.exists(_env_path):
        with open(_env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k == "HOME_ASSISTANT_URL" and not HA_URL:
                    HA_URL = v
                elif k == "HOME_ASSISTANT_TOKEN" and not HA_TOKEN:
                    HA_TOKEN = v

if not HA_URL:
    HA_URL = "http://192.168.4.124:8123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class Result:
    def __init__(self, name):
        self.name = name
        self.passed = False
        self.detail = ""
        self.error = ""

    def ok(self, detail=""):
        self.passed = True
        self.detail = detail
        return self

    def fail(self, error):
        self.passed = False
        self.error = error
        return self


def ha_get_state(entity_id):
    """Fetch a single entity state from the HA REST API."""
    url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("state", "")
    except Exception as e:
        return f"ERROR: {e}"


def print_header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(r):
    status = "PASS ✅" if r.passed else "FAIL ❌"
    print(f"\n  [{status}] {r.name}")
    if r.detail:
        for line in r.detail.splitlines():
            print(f"    {line}")
    if r.error:
        print(f"    ERROR: {r.error}")


# ---------------------------------------------------------------------------
# Fallback implementations when threemf_parser can't be imported
# ---------------------------------------------------------------------------

def _fallback_list_dir(printer_ip, access_code, directory, port, timeout=15):
    """curl-based directory listing fallback."""
    import subprocess
    dir_path = directory.rstrip("/") + "/"
    url = f"ftps://{printer_ip}:{port}{dir_path}"
    cmd = [
        "curl", "--ssl-reqd", "--insecure", "--user", f"bblp:{access_code}",
        "--list-only", "--max-time", str(timeout), url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        if result.returncode != 0:
            return []
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        files = [l.strip().strip("\r") for l in lines if l.strip()]
        return [f for f in files if f.lower().endswith(".3mf")]
    except Exception:
        return []


def _fallback_download(printer_ip, access_code, filename, dest_dir, port, directory, timeout=30):
    """curl-based download fallback."""
    import subprocess
    import urllib.parse as up
    local_path = os.path.join(dest_dir, filename)
    dir_path = directory.rstrip("/")
    encoded = up.quote(filename, safe="")
    url = f"ftps://{printer_ip}:{port}{dir_path}/{encoded}"
    cmd = [
        "curl", "--ssl-reqd", "--insecure", "--globoff",
        "--user", f"bblp:{access_code}", "--output", local_path,
        "--max-time", str(timeout), url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        if result.returncode == 0 and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return local_path
        return None
    except Exception:
        return None


def _fallback_find_best(file_list, task_name):
    """Simple fallback matching."""
    if not file_list:
        return None
    if not task_name:
        return file_list[-1]
    task_lower = task_name.lower()
    for f in file_list:
        if task_lower in f.lower() or f.lower().replace(".3mf", "") in task_lower:
            return f
    return file_list[-1]


def _fallback_parse(local_path):
    """Minimal 3MF parse fallback."""
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(local_path, "r") as zf:
            config_path = None
            for name in zf.namelist():
                if "slice_info" in name.lower() and name.endswith(".config"):
                    config_path = name
                    break
            if not config_path:
                return []
            with zf.open(config_path) as f:
                root = ET.fromstring(f.read())
            filaments = []
            for elem in root.iter("filament"):
                filaments.append({
                    "index": int(elem.get("id", len(filaments))),
                    "used_g": float(elem.get("used_g", 0)),
                    "used_m": float(elem.get("used_m", 0)),
                    "color_hex": elem.get("color", ""),
                    "material": elem.get("type", ""),
                    "tray_info_idx": elem.get("tray_info_idx", ""),
                })
            return filaments
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Dispatch — use imported functions or fallbacks
# ---------------------------------------------------------------------------

def do_list_dir(ip, code, directory, port):
    if PARSER_IMPORTED:
        return ftps_list_dir(ip, code, directory, port)
    return _fallback_list_dir(ip, code, directory, port)


def do_list_cache(ip, code, port):
    if PARSER_IMPORTED:
        return ftps_list_cache(ip, code, port)
    # Fallback: search dirs manually
    for d in SEARCH_DIRS:
        files = _fallback_list_dir(ip, code, d, port)
        if files:
            return files, d
    return [], None


def do_download(ip, code, filename, dest_dir, port, directory):
    if PARSER_IMPORTED:
        return ftps_download_3mf(ip, code, filename, dest_dir, port, directory=directory)
    return _fallback_download(ip, code, filename, dest_dir, port, directory)


def do_find_best(file_list, task_name):
    if PARSER_IMPORTED:
        return find_best_3mf(file_list, task_name)
    return _fallback_find_best(file_list, task_name)


def do_parse(local_path):
    if PARSER_IMPORTED:
        return parse_3mf_filaments(local_path)
    return _fallback_parse(local_path)


# ---------------------------------------------------------------------------
# Test stages
# ---------------------------------------------------------------------------

def test_ftps_connection(access_code):
    """Stage 1: FTPS connection and directory listing."""
    r = Result("FTPS Connection & Directory Listing")
    lines = []
    all_files = {}

    for d in SEARCH_DIRS:
        files = do_list_dir(PRINTER_IP, access_code, d, PRINTER_PORT)
        all_files[d] = files
        count = len(files)
        status = f"{count} .3mf file(s)" if count > 0 else "empty or inaccessible"
        lines.append(f"{d}: {status}")
        if files and count <= 5:
            for f in files:
                lines.append(f"    {f}")
        elif files:
            for f in files[:3]:
                lines.append(f"    {f}")
            lines.append(f"    ... and {count - 3} more")

    total = sum(len(v) for v in all_files.values())
    if total == 0:
        return r.fail("No .3mf files found in any directory"), all_files

    lines.insert(0, f"Total: {total} .3mf file(s) across {sum(1 for v in all_files.values() if v)} dir(s)")
    return r.ok("\n".join(lines)), all_files


def test_file_matching(file_list, task_name):
    """Stage 2: File matching against task name and unicode test cases."""
    r = Result("File Matching")
    lines = []

    if not PARSER_IMPORTED:
        lines.append("(threemf_parser not imported — using fallback matching, tier detection unavailable)")

    # Real task match
    best = do_find_best(file_list, task_name)
    if PARSER_IMPORTED:
        tier = _detect_match_tier(file_list, task_name, best)
    else:
        tier = "unknown (fallback)"
    lines.append(f"Task: {task_name!r}")
    lines.append(f"Match: {best!r} (tier: {tier})")

    # Unicode test cases
    lines.append("")
    lines.append("Unicode normalization tests:")
    for case in UNICODE_TEST_CASES:
        match = do_find_best(file_list, case)
        if PARSER_IMPORTED:
            norm = normalize_task_name(case)
            tier = _detect_match_tier(file_list, case, match)
            lines.append(f"  {case!r}")
            lines.append(f"    normalized: {norm!r}")
            lines.append(f"    match: {match!r} (tier: {tier})")
        else:
            lines.append(f"  {case!r} -> {match!r}")

    return r.ok("\n".join(lines)), best


def _detect_match_tier(file_list, task_name, matched):
    """Determine which tier of find_best_3mf matched."""
    if not matched or not file_list:
        return "none"
    if not task_name:
        return "fallback (no task name)"

    norm_task = normalize_task_name(task_name)
    # Tier 1: exact normalized
    for f in file_list:
        if normalize_task_name(f) == norm_task:
            if f == matched:
                return "1-exact"
            break

    # Tier 2: contains
    for f in file_list:
        norm_f = normalize_task_name(f)
        if norm_task in norm_f or norm_f in norm_task:
            if f == matched:
                return "2-contains"
            break

    # Tier 3: fallback (last in list)
    if matched == file_list[-1]:
        return "3-fallback"

    return "unknown"


def test_download(access_code, filename, found_dir, tmp_dir):
    """Stage 3: Download and validate the file."""
    r = Result(f"Download: {filename}")

    local_path = do_download(
        PRINTER_IP, access_code, filename, tmp_dir, PRINTER_PORT, found_dir,
    )
    if not local_path:
        return r.fail(f"Download failed for {filename!r} from {found_dir}"), None

    size = os.path.getsize(local_path)
    lines = [f"File: {local_path}", f"Size: {size:,} bytes"]

    # Validate it's a valid zip
    try:
        with zipfile.ZipFile(local_path, "r") as zf:
            names = zf.namelist()
            lines.append(f"Valid ZIP: {len(names)} entries")
            slice_configs = [n for n in names if "slice_info" in n.lower()]
            if slice_configs:
                lines.append(f"Slice config: {slice_configs[0]}")
            else:
                lines.append("WARNING: No slice_info.config found in archive")
    except zipfile.BadZipFile as e:
        return r.fail(f"Not a valid ZIP: {e}"), None

    return r.ok("\n".join(lines)), local_path


def test_parse(local_path):
    """Stage 4: Parse filament data from the 3MF."""
    r = Result("3MF Parse")

    filaments = do_parse(local_path)
    if not filaments:
        return r.fail("No filament data extracted")

    lines = [f"Filaments: {len(filaments)}"]
    total_g = 0.0
    for f in filaments:
        total_g += f.get("used_g", 0)
        lines.append(
            f"  [{f.get('index', '?')}] {f.get('used_g', 0):.2f}g "
            f"/ {f.get('used_m', 0):.2f}m  "
            f"material={f.get('material', '?')}  "
            f"color={f.get('color_hex', '?')}  "
            f"tray_idx={f.get('tray_info_idx', '?')}"
        )
    lines.append(f"Total: {total_g:.2f}g")

    # Validate structure
    required_keys = {"index", "used_g", "used_m", "color_hex", "material", "tray_info_idx"}
    for f in filaments:
        missing = required_keys - set(f.keys())
        if missing:
            return r.fail(f"Filament entry missing keys: {missing}")

    return r.ok("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="3MF Pipeline Diagnostic")
    parser.add_argument("--task", help="Task name to match (default: from HA entity)")
    parser.add_argument("--access-code", help="Printer access code (default: from HA entity)")
    parser.add_argument("--method", choices=["curl", "native", "both"], default="both",
                        help="FTPS method to test (default: both)")
    args = parser.parse_args()

    print_header("3MF Pipeline Diagnostic")
    print(f"  Printer: {PRINTER_IP}:{PRINTER_PORT}")
    print(f"  Parser imported: {PARSER_IMPORTED}")
    print(f"  HA URL: {HA_URL}")

    results = []

    # Get access code
    access_code = args.access_code
    if not access_code:
        print(f"\n  Fetching access code from {ACCESS_CODE_ENTITY}...")
        access_code = ha_get_state(ACCESS_CODE_ENTITY)
        if access_code.startswith("ERROR:") or not access_code or access_code in ("unknown", "unavailable"):
            print(f"  FATAL: Could not get access code: {access_code}")
            print("  Use --access-code to provide it manually.")
            sys.exit(1)
        print(f"  Access code: {'*' * (len(access_code) - 2)}{access_code[-2:]}")

    # Get task name
    task_name = args.task
    if not task_name:
        print(f"  Fetching task name from {TASK_NAME_ENTITY}...")
        task_name = ha_get_state(TASK_NAME_ENTITY)
        if task_name.startswith("ERROR:"):
            print(f"  WARNING: Could not get task name: {task_name}")
            task_name = ""
        else:
            print(f"  Task name: {task_name!r}")

    use_curl = args.method in ("curl", "both")
    use_native = args.method in ("native", "both") and NATIVE_AVAILABLE

    if args.method in ("native", "both") and not NATIVE_AVAILABLE:
        print("\n  WARNING: Native FTPS not available (import failed)")
        if args.method == "native":
            sys.exit(1)

    # ── curl-based pipeline ───────────────────────────────────────────
    found_dir = None
    file_list = []

    if use_curl:
        # Stage 1: FTPS Connection (curl)
        print_header("Stage 1: FTPS Connection (curl)")
        r1, all_files = test_ftps_connection(access_code)
        print_result(r1)
        results.append(r1)

        for d in SEARCH_DIRS:
            if all_files.get(d):
                found_dir = d
                file_list = all_files[d]
                break

        if not file_list:
            print("\n  Skipping curl download stages (no files found)")
        else:
            print_header("Stage 2: File Matching")
            r2, best_file = test_file_matching(file_list, task_name)
            print_result(r2)
            results.append(r2)

            download_target = best_file or file_list[0]

            print_header("Stage 3: Download (curl)")
            with tempfile.TemporaryDirectory(prefix="3mf_diag_") as tmp_dir:
                r3, local_path = test_download(access_code, download_target, found_dir, tmp_dir)
                print_result(r3)
                results.append(r3)

                if local_path:
                    print_header("Stage 4: Parse")
                    r4 = test_parse(local_path)
                    print_result(r4)
                    results.append(r4)

    # ── native ftplib pipeline ────────────────────────────────────────
    if use_native:
        import time as _time

        print_header("Stage 5: FTPS Connection (native ftplib)")
        r5 = Result("FTPS Native Connection")
        t0 = _time.monotonic()
        try:
            conn = ftps_connect(PRINTER_IP, access_code, PRINTER_PORT)
            elapsed = _time.monotonic() - t0
            r5.ok(f"Connected in {elapsed:.2f}s  welcome={conn.getwelcome()!r}")
        except Exception as e:
            elapsed = _time.monotonic() - t0
            r5.fail(f"Connection failed after {elapsed:.2f}s: {e}")
            conn = None
        print_result(r5)
        results.append(r5)

        if conn:
            print_header("Stage 6: Directory Listing (native)")
            r6 = Result("FTPS Native Listing")
            t0 = _time.monotonic()
            native_files, native_dir = ftps_list_cache_native(conn)
            elapsed = _time.monotonic() - t0
            if native_files:
                detail = (
                    f"Found {len(native_files)} .3mf file(s) in {native_dir} "
                    f"({elapsed:.2f}s)"
                )
                if len(native_files) <= 5:
                    detail += "\n" + "\n".join(f"  {f}" for f in native_files)
                else:
                    detail += "\n" + "\n".join(f"  {f}" for f in native_files[:3])
                    detail += f"\n  ... and {len(native_files) - 3} more"
                r6.ok(detail)
            else:
                r6.fail(f"No .3mf files found ({elapsed:.2f}s)")
            print_result(r6)
            results.append(r6)

            if native_files:
                native_best = do_find_best(native_files, task_name)
                download_target = native_best or native_files[0]

                print_header("Stage 7: Download (native)")
                r7 = Result(f"FTPS Native Download: {download_target}")
                with tempfile.TemporaryDirectory(prefix="3mf_native_") as tmp_dir:
                    t0 = _time.monotonic()
                    local_path = ftps_download_native(
                        conn, native_dir, download_target,
                        os.path.join(tmp_dir, download_target),
                    )
                    elapsed = _time.monotonic() - t0
                    if local_path:
                        size = os.path.getsize(local_path)
                        r7.ok(f"Downloaded {size:,} bytes in {elapsed:.2f}s")
                    else:
                        r7.fail(f"Download failed ({elapsed:.2f}s)")
                    print_result(r7)
                    results.append(r7)

                    if local_path:
                        print_header("Stage 8: Parse (native download)")
                        r8 = test_parse(local_path)
                        print_result(r8)
                        results.append(r8)

            # Compare curl vs native file lists
            if use_curl and file_list and native_files:
                print_header("Comparison: curl vs native")
                curl_set = set(file_list)
                native_set = set(native_files)
                if curl_set == native_set:
                    print(f"  MATCH: Both methods found {len(curl_set)} identical files")
                else:
                    print(f"  MISMATCH:")
                    print(f"    curl only:   {curl_set - native_set}")
                    print(f"    native only: {native_set - curl_set}")

            try:
                conn.quit()
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass

    # Summary
    print_header("Summary")
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.name}")
    print(f"\n  {passed}/{total} stages passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
