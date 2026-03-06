"""
3MF Parser — extracts per-filament usage from Bambu 3MF files.

The 3MF is a ZIP containing Metadata/slice_info.config (XML) with:
  <filament id="N" type="PLA" color="#RRGGBBAA" used_m="..." used_g="..." .../>

This module is pure utility — no AppDaemon or HA dependencies.
"""

import logging
import os
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

logger = logging.getLogger(__name__)


def normalize_color(raw):
    """Normalize color to 6-char lowercase hex. Drops alpha channel.
    '#00AE42FF' -> '00ae42', '000000' -> '000000', None -> ''
    """
    if not raw:
        return ""
    raw = str(raw).strip().lstrip("#")
    if len(raw) == 8:
        raw = raw[:6]
    return raw.lower() if len(raw) == 6 else ""


def normalize_material(raw):
    """Normalize material string: strip, lower."""
    if not raw:
        return ""
    return str(raw).strip().lower()


def normalize_task_name(name):
    """Normalize task/filename for matching:
    - lowercase
    - strip extensions (.3mf, .gcode.3mf, .gcode)
    - replace [ _-]+ with single space
    - strip
    """
    if not name:
        return ""
    name = str(name).lower()
    for ext in [".gcode.3mf", ".3mf", ".gcode"]:
        if name.endswith(ext):
            name = name[: -len(ext)]
    name = re.sub(r"[_\- ]+", " ", name).strip()
    return name


def _materials_match(mat1, mat2):
    """Fuzzy material matching. PLA+ matches PLA, PETG-CF matches PETG, etc."""
    if not mat1 or not mat2:
        return mat1 == mat2
    if mat1 == mat2:
        return True
    # Strip suffixes: PLA+ → PLA, PETG-CF → PETG
    base1 = mat1.rstrip("+").split("-")[0].strip()
    base2 = mat2.rstrip("+").split("-")[0].strip()
    return base1 == base2


def color_distance(hex1, hex2):
    """Euclidean distance between two 6-char hex colors in RGB space.
    Returns 0.0 for exact match, up to ~441.7 for black vs white.
    """
    if not hex1 or not hex2 or len(hex1) != 6 or len(hex2) != 6:
        return 999.0
    try:
        r1, g1, b1 = int(hex1[0:2], 16), int(hex1[2:4], 16), int(hex1[4:6], 16)
        r2, g2, b2 = int(hex2[0:2], 16), int(hex2[2:4], 16), int(hex2[4:6], 16)
        return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5
    except (ValueError, IndexError):
        return 999.0


def ftps_list_cache(printer_ip, access_code, port=990, timeout=15):
    """List .3mf files in the printer's /cache/ directory via FTPS.
    Returns list of filenames (strings), newest first if MLSD available.
    """
    url = f"ftps://{printer_ip}:{port}/cache/"
    cmd = [
        "curl",
        "--ssl-reqd",
        "--insecure",
        "--user",
        f"bblp:{access_code}",
        "--list-only",
        "--max-time",
        str(timeout),
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        if result.returncode != 0:
            logger.error(
                f"FTPS list failed: {result.stderr.decode('utf-8', errors='replace')}"
            )
            return []
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        files = [line.strip().strip("\r") for line in lines if line.strip()]
        files = [f for f in files if f.lower().endswith(".3mf")]
        return files
    except subprocess.TimeoutExpired:
        logger.error("FTPS list timed out")
        return []
    except FileNotFoundError:
        logger.error("curl not found — cannot fetch 3MF from printer")
        return []


def ftps_download_3mf(
    printer_ip, access_code, remote_filename, dest_dir, port=990, timeout=30
):
    """Download a 3MF file from the printer's /cache/ directory.
    Returns local file path on success, None on failure.

    Uses curl CWD + RETR to avoid URL-encoding issues with spaces,
    unicode, and emoji in filenames.
    """
    local_path = os.path.join(dest_dir, remote_filename)
    base_url = f"ftps://{printer_ip}:{port}/cache/"

    cmd = [
        "curl",
        "--ssl-reqd",
        "--insecure",
        "--user",
        f"bblp:{access_code}",
        "--output",
        local_path,
        "--max-time",
        str(timeout),
        "-Q",
        f"CWD /cache",
        "-Q",
        f"RETR {remote_filename}",
        base_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        if (
            result.returncode == 0
            and os.path.exists(local_path)
            and os.path.getsize(local_path) > 0
        ):
            return local_path

        # Fallback: try with --globoff and raw URL (works for some curl versions)
        url_raw = f"ftps://{printer_ip}:{port}/cache/{remote_filename}"
        cmd_fallback = [
            "curl",
            "--ssl-reqd",
            "--insecure",
            "--globoff",
            "--user",
            f"bblp:{access_code}",
            "--output",
            local_path,
            "--max-time",
            str(timeout),
            url_raw,
        ]
        result = subprocess.run(
            cmd_fallback, capture_output=True, timeout=timeout + 5
        )
        if (
            result.returncode == 0
            and os.path.exists(local_path)
            and os.path.getsize(local_path) > 0
        ):
            return local_path

        logger.error(
            f"FTPS download failed for '{remote_filename}': "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )
        return None
    except subprocess.TimeoutExpired:
        logger.error(f"FTPS download timed out for '{remote_filename}'")
        return None
    except FileNotFoundError:
        logger.error("curl not found — cannot fetch 3MF from printer")
        return None


def find_best_3mf(file_list, task_name):
    """Match a task name to the best 3MF filename.
    Returns the filename or None.

    Match priority:
    1. Exact normalized match
    2. Contains match (either direction)
    3. Newest (last in list, assuming alphabetical or MLSD order)
    """
    if not file_list:
        return None
    if not task_name:
        return file_list[-1] if file_list else None

    norm_task = normalize_task_name(task_name)

    for f in file_list:
        if normalize_task_name(f) == norm_task:
            return f

    for f in file_list:
        norm_f = normalize_task_name(f)
        if norm_task in norm_f or norm_f in norm_task:
            return f

    return file_list[-1]


def parse_3mf_filaments(local_path):
    """Parse a 3MF file and extract per-filament usage.

    Returns list of dicts:
    [
        {"index": 0, "used_g": 1.29, "used_m": 0.43, "color_hex": "00ae42",
         "material": "pla", "tray_info_idx": "0"},
        ...
    ]

    Returns empty list on any error.
    """
    try:
        with zipfile.ZipFile(local_path, "r") as zf:
            config_path = None
            for candidate in [
                "Metadata/slice_info.config",
                "Metadata/Slice_info.config",
            ]:
                if candidate in zf.namelist():
                    config_path = candidate
                    break

            if config_path is None:
                for name in zf.namelist():
                    if "slice_info" in name.lower() and name.endswith(".config"):
                        config_path = name
                        break

            if config_path is None:
                logger.error(f"No slice_info.config found in {local_path}")
                return []

            with zf.open(config_path) as f:
                xml_content = f.read()

        root = ET.fromstring(xml_content)

        filaments = []
        for elem in root.iter("filament"):
            try:
                idx = int(elem.get("id", len(filaments)))
                used_g = float(elem.get("used_g", 0))
                used_m = float(elem.get("used_m", 0))
                color = normalize_color(elem.get("color", ""))
                material = normalize_material(elem.get("type", ""))
                tray_idx = elem.get("tray_info_idx", "")

                filaments.append(
                    {
                        "index": idx,
                        "used_g": used_g,
                        "used_m": used_m,
                        "color_hex": color,
                        "material": material,
                        "tray_info_idx": tray_idx,
                    }
                )
            except (ValueError, TypeError) as e:
                logger.warning(f"Skipping malformed filament element: {e}")
                continue

        filaments.sort(key=lambda f: f["index"])
        return filaments

    except (zipfile.BadZipFile, ET.ParseError, OSError) as e:
        logger.error(f"Failed to parse 3MF {local_path}: {e}")
        return []


def match_filaments_to_slots(filaments, slot_data, trays_used=None):
    """Match 3MF filament entries to physical AMS slots.

    Args:
        filaments: list of dicts from parse_3mf_filaments
        slot_data: dict of {slot_int: {"color_hex": str, "material": str, "spool_id": int}}
        trays_used: set of slot ints that were active during print (for validation)

    Returns:
        matches: list of {"slot": int, "spool_id": int, "used_g": float, "filament_index": int, "method": str}
        unmatched: list of filament dicts that couldn't be matched

    Matching strategy:
    1. Exact color + material match
    2. Close color (distance < 30) + material match
    3. Material-only match (if only one candidate)
    Each slot can only be matched once.
    """
    matches = []
    unmatched = []
    used_slots = set()

    available_slots = {}
    for slot, data in slot_data.items():
        if trays_used and slot not in trays_used:
            continue
        if data.get("spool_id", 0) <= 0:
            continue
        available_slots[slot] = data

    for fil in filaments:
        if fil["used_g"] <= 0:
            continue

        fil_color = fil["color_hex"]
        fil_material = fil["material"]
        best_slot = None
        best_method = None
        best_distance = 999.0

        for slot, data in available_slots.items():
            if slot in used_slots:
                continue
            if _materials_match(data["material"], fil_material) and data["color_hex"] == fil_color:
                best_slot = slot
                best_method = "exact_color_material"
                best_distance = 0.0
                break

        if best_slot is None:
            for slot, data in available_slots.items():
                if slot in used_slots:
                    continue
                if not _materials_match(data["material"], fil_material):
                    continue
                dist = color_distance(fil_color, data["color_hex"])
                if dist < 30 and dist < best_distance:
                    best_slot = slot
                    best_method = f"close_color_material(dist={dist:.1f})"
                    best_distance = dist

        if best_slot is None:
            material_candidates = [
                s
                for s, d in available_slots.items()
                if s not in used_slots and _materials_match(d["material"], fil_material)
            ]
            # Only use material_only when exactly one slot of this material exists
            # in the full slot_data — e.g. one PETG slot in system. If multiple
            # slots have this material (even if one excluded), require color match.
            n_material_total = sum(
                1 for d in slot_data.values() if _materials_match(d["material"], fil_material)
            )
            if len(material_candidates) == 1 and n_material_total == 1:
                best_slot = material_candidates[0]
                best_method = "material_only_single"

        if best_slot is not None:
            used_slots.add(best_slot)
            matches.append(
                {
                    "slot": best_slot,
                    "spool_id": available_slots[best_slot]["spool_id"],
                    "used_g": fil["used_g"],
                    "filament_index": fil["index"],
                    "method": best_method,
                }
            )
        else:
            unmatched.append(fil)

    return matches, unmatched
