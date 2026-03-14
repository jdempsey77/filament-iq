"""
3MF Parser — extracts per-filament usage from Bambu 3MF files.

The 3MF is a ZIP containing Metadata/slice_info.config (XML) with:
  <filament id="N" type="PLA" color="#RRGGBBAA" used_m="..." used_g="..." .../>

This module is pure utility — no AppDaemon or HA dependencies.
"""

import ftplib
import logging
import os
import re
import socket
import ssl
import subprocess
import unicodedata
import urllib.parse
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
    - NFC unicode normalization (handles NFD decomposed umlauts etc.)
    - lowercase
    - strip extensions (.3mf, .gcode.3mf, .gcode)
    - replace unicode dashes (en dash, em dash, etc.) with ASCII hyphen
    - replace non-letter/non-digit/non-ASCII-punctuation with space
      (strips symbols like ●★◉︎ but preserves umlauts, accented letters)
    - collapse whitespace/underscores/hyphens to single space
    - strip
    """
    if not name:
        return ""
    name = unicodedata.normalize("NFC", str(name)).lower()
    for ext in [".gcode.3mf", ".3mf", ".gcode"]:
        if name.endswith(ext):
            name = name[: -len(ext)]
    # Replace unicode dashes (en dash, em dash, figure dash, horizontal bar,
    # minus sign, etc.) with ASCII hyphen
    name = re.sub(r"[\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]", "-", name)
    # Replace non-letter, non-digit, non-basic-punctuation characters with
    # space. \w covers letters+digits+underscore in Unicode, so this keeps
    # umlauts/accented chars. We also keep . , % ( ) for slicer suffixes.
    name = re.sub(r"[^\w.,%()\-\s]", " ", name)
    # Collapse runs of whitespace, underscores, and hyphens to single space
    name = re.sub(r"[_\- \t]+", " ", name).strip()
    return name


def parse_lot_nr_color(lot_nr):
    """Extract color_hex from a non-RFID lot_nr signature (type|filament_id|color_hex).
    Returns normalized 6-char lowercase hex, or '' if not a valid signature.
    RFID lot_nr values (32-char hex UUIDs like tray_uuid) are skipped.
    """
    if not lot_nr:
        return ""
    lot_nr = str(lot_nr).strip()
    # RFID: 32-char hex UUID (tray_uuid) — no color to extract
    if len(lot_nr) == 32 and all(c in "0123456789abcdefABCDEF" for c in lot_nr):
        return ""
    parts = lot_nr.split("|")
    if len(parts) >= 3:
        return normalize_color(parts[2])
    return ""


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


FTPS_SEARCH_DIRS = ["/cache", "/model", "/sdcard"]


# ── Native ftplib transport (implicit TLS) ─────────────────────────────


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """FTP_TLS subclass for implicit TLS (port 990).

    Standard ftplib.FTP_TLS uses explicit TLS (AUTH TLS after connect).
    Bambu printers use implicit TLS where the connection is wrapped in
    SSL/TLS from the start. This subclass overrides connect() to wrap
    the socket immediately, and ntransfercmd() to handle TLS session
    reuse on the data channel (required by Bambu's FTPS server).
    """

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address

        self.sock = socket.create_connection(
            (self.host, self.port), self.timeout, self.source_address
        )
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        """Override to reuse TLS session on the data channel.

        Some FTP servers (including Bambu) require TLS session reuse —
        the data channel must present the same TLS session as the control
        channel. Without this, RETR commands hang waiting for the server
        to accept the data connection.
        """
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            # Reuse the control channel's TLS session on the data socket
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=self.sock.session,
            )
        return conn, size


def ftps_connect(printer_ip, access_code, port=990, timeout=15):
    """Establish a persistent implicit-TLS FTPS connection to the printer.

    Returns a connected ImplicitFTP_TLS instance, logged in as bblp,
    with data channel protection enabled (PROT P).
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # Bambu uses self-signed cert

    ftp = ImplicitFTP_TLS(context=ctx)
    ftp.connect(printer_ip, port, timeout=timeout)
    ftp.login("bblp", access_code)
    ftp.prot_p()
    return ftp


def ftps_list_cache_native(ftp_conn, search_dirs=None):
    """List .3mf files using an existing FTP connection.

    Searches directories in order, returns (files, directory) on first hit.
    Single TLS session — no extra handshake per directory.
    """
    if search_dirs is None:
        search_dirs = FTPS_SEARCH_DIRS
    for directory in search_dirs:
        try:
            raw_list = ftp_conn.nlst(directory)
            # nlst may return full paths (/cache/file.3mf) or just filenames
            files = []
            for entry in raw_list:
                basename = entry.rsplit("/", 1)[-1] if "/" in entry else entry
                if basename.lower().endswith(".3mf"):
                    files.append(basename)
            if files:
                logger.info(f"FTPS_NATIVE found {len(files)} .3mf file(s) in {directory}")
                return files, directory
        except ftplib.error_perm as e:
            logger.debug(f"FTPS_NATIVE list {directory}: {e}")
            continue
    return [], None


def ftps_download_native(ftp_conn, remote_dir, remote_filename, local_path):
    """Download a file using an existing FTP connection.

    Uses RETR on the same TLS session — no extra handshake.
    Returns local_path on success, None on failure.
    """
    dir_path = remote_dir.rstrip("/")
    remote_path = f"{dir_path}/{remote_filename}"
    try:
        with open(local_path, "wb") as f:
            ftp_conn.retrbinary(f"RETR {remote_path}", f.write)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return local_path
        logger.warning("FTPS_NATIVE_DOWNLOAD empty file for '%s'", remote_filename)
        return None
    except (ftplib.error_perm, ftplib.error_temp, OSError) as e:
        logger.warning("FTPS_NATIVE_DOWNLOAD_FAILED file='%s' error=%s", remote_filename, e)
        return None


# ── Legacy curl-based transport (fallback) ─────────────────────────────


def ftps_list_dir(printer_ip, access_code, directory="/cache", port=990, timeout=15):
    """List .3mf files in a printer directory via FTPS.
    Returns list of filenames (strings).
    """
    # Ensure directory has trailing slash for curl
    dir_path = directory.rstrip("/") + "/"
    url = f"ftps://{printer_ip}:{port}{dir_path}"
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
            logger.debug(
                f"FTPS list {directory}: {result.stderr.decode('utf-8', errors='replace').strip()}"
            )
            return []
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        files = [line.strip().strip("\r") for line in lines if line.strip()]
        files = [f for f in files if f.lower().endswith(".3mf")]
        return files
    except subprocess.TimeoutExpired:
        logger.error(f"FTPS list timed out for {directory}")
        return []
    except FileNotFoundError:
        logger.error("curl not found — cannot fetch 3MF from printer")
        return []


def ftps_list_cache(printer_ip, access_code, port=990, timeout=15):
    """List .3mf files, searching multiple directories on the printer.
    Tries /cache, /model, /sdcard in order. Returns (files, directory) tuple.
    Falls back to ([], None) if nothing found anywhere.
    """
    for directory in FTPS_SEARCH_DIRS:
        files = ftps_list_dir(printer_ip, access_code, directory, port, timeout)
        if files:
            logger.info(f"FTPS found {len(files)} .3mf file(s) in {directory}")
            return files, directory
    return [], None


def ftps_download_3mf(
    printer_ip, access_code, remote_filename, dest_dir, port=990, timeout=30,
    directory="/cache",
):
    """Download a 3MF file from a printer directory via FTPS.
    Returns local file path on success, None on failure.

    URL-encodes the filename and uses --globoff to handle spaces, unicode,
    brackets, and other special characters in Bambu filenames.
    """
    local_path = os.path.join(dest_dir, remote_filename)
    dir_path = directory.rstrip("/")
    encoded_name = urllib.parse.quote(remote_filename, safe="")
    url = f"ftps://{printer_ip}:{port}{dir_path}/{encoded_name}"

    cmd = [
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
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
        if (
            result.returncode == 0
            and os.path.exists(local_path)
            and os.path.getsize(local_path) > 0
        ):
            return local_path

        logger.warning(
            "FTPS_DOWNLOAD_FAILED file='%s' rc=%d stderr='%s'",
            remote_filename, result.returncode, stderr_text,
        )
        return None
    except subprocess.TimeoutExpired:
        logger.warning("FTPS_DOWNLOAD_TIMEOUT file='%s'", remote_filename)
        return None
    except FileNotFoundError:
        logger.warning("FTPS_DOWNLOAD_NO_CURL curl not found")
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
    2.5. Exact lot_nr_color + material match (fallback when tray/Spoolman color differs)
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

    # Single-filament force match: if exactly one tray active and
    # exactly one filament in 3MF, match directly — index and color
    # are irrelevant when there is no ambiguity.
    active_filaments = [f for f in filaments if f["used_g"] > 0]
    if (
        trays_used
        and len(trays_used) == 1
        and len(active_filaments) == 1
        and available_slots
    ):
        slot = next(iter(trays_used))
        if slot in available_slots:
            fil = active_filaments[0]
            matches.append({
                "slot": slot,
                "spool_id": available_slots[slot]["spool_id"],
                "used_g": fil["used_g"],
                "filament_index": fil["index"],
                "method": "single_filament_force",
            })
            return matches, []

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

        # Tier 2.5: exact lot_nr_color + material match
        if best_slot is None:
            for slot, data in available_slots.items():
                if slot in used_slots:
                    continue
                lot_color = data.get("lot_nr_color", "")
                if lot_color and _materials_match(data["material"], fil_material) and lot_color == fil_color:
                    best_slot = slot
                    best_method = "lot_nr_color_material"
                    best_distance = 0.0
                    break

        # Tier 2.75: slot_position_material — filament index maps to AMS slot
        if best_slot is None:
            position_slot = fil["index"]
            if (
                position_slot in available_slots
                and position_slot not in used_slots
                and _materials_match(available_slots[position_slot]["material"], fil_material)
            ):
                best_slot = position_slot
                best_method = "slot_position_material"

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
