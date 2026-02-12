"""
FTPS client for Bambu P1/A1: list and download 3MF files.
Implicit TLS on port 990; credentials: user bblp, password = printer access code.
Uses curl with --ssl-reqd --insecure --list-only for listing.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote


FTPS_PORT = 990
FTPS_USER = "bblp"
# P1S stores 3MF under /cache/ (verified on real printer)
CACHE_DIR = "/cache/"


def _normalize_task_name(name: str) -> str:
    """Normalize for matching: lowercase, collapse spaces/underscores, strip extension."""
    if not name:
        return ""
    s = name.strip().lower()
    # Remove common extensions
    for ext in (".3mf", ".gcode.3mf", ".gcode"):
        if s.endswith(ext):
            s = s[: -len(ext)]
            break
    s = re.sub(r"[\s_]+", "", s)
    return s


def _normalize_filename(fname: str) -> str:
    """Normalize filename to compare with task_name (no path, no extension)."""
    base = os.path.basename(fname).lower()
    for ext in (".gcode.3mf", ".3mf", ".gcode"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    base = re.sub(r"[\s_]+", "", base)
    return base


def _curl_ftps_list(host: str, password: str, directory: str) -> list[str]:
    """
    List files via curl FTPS (implicit TLS). Uses --ssl-reqd --insecure --list-only.
    Returns list of raw filenames (one per line); decode as UTF-8, strip CRLF.
    """
    url = f"ftps://{host}:{FTPS_PORT}{directory}"
    cmd = [
        "curl",
        "--ssl-reqd",
        "--insecure",
        "-s",
        "--connect-timeout", "10",
        "-u", f"{FTPS_USER}:{password}",
        "--list-only",
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if result.returncode != 0:
        return []

    out: list[str] = []
    for line in result.stdout.splitlines():
        name = line.strip().rstrip("\r").strip()
        if name:
            out.append(name)
    return out


def _curl_ftps_download(host: str, password: str, remote_path: str, local_path: str) -> bool:
    """Download one file via curl FTPS. remote_path e.g. /cache/File.3mf (spaces/UTF-8 encoded in URL)."""
    # URL-encode path so spaces and non-ASCII work; safe='/' keeps path slashes
    quoted = quote(remote_path, safe="/")
    url = f"ftps://{host}:{FTPS_PORT}{quoted}"
    cmd = [
        "curl",
        "--ssl-reqd",
        "--insecure",
        "-s",
        "--connect-timeout", "10",
        "-u", f"{FTPS_USER}:{password}",
        "-o", local_path,
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60, encoding="utf-8", errors="replace")
        return result.returncode == 0 and os.path.isfile(local_path) and os.path.getsize(local_path) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def list_3mf_files(host: str, password: str) -> tuple[list[tuple[str, str, int | None]], list[str]]:
    """
    List *.3mf files from printer /cache/ (P1S verified).
    Filter: filename.endswith(".3mf") case-insensitive (includes .gcode.3mf).
    Returns (filtered_list, raw_all_filenames). filtered_list items are (directory, filename, mtime).
    Full remote path = directory + filename = /cache/<filename> (exact filename including spaces/UTF-8).
    """
    raw = _curl_ftps_list(host, password, CACHE_DIR)
    results: list[tuple[str, str, int | None]] = []
    for fname in raw:
        if fname.lower().endswith(".3mf"):
            results.append((CACHE_DIR, fname, None))
    return results, raw


def pick_best_3mf(
    host: str,
    password: str,
    task_name: str,
    file_list: list[tuple[str, str, int | None]] | None = None,
) -> tuple[str | None, str]:
    """
    Pick the best 3MF for the given task_name.
    If file_list is None, fetches list from printer.
    Returns (remote_path, note). remote_path is e.g. /cache/File.3mf or None.
    """
    if file_list is None:
        file_list, _ = list_3mf_files(host, password)
    if not file_list:
        return None, "No 3MF files found on printer"

    norm_task = _normalize_task_name(task_name)
    if not norm_task:
        if len(file_list) == 1:
            d, f = file_list[0][0], file_list[0][1]
            path = (d.rstrip("/") + "/" + f).lstrip("/")
            return "/" + path, "Single file (no task_name match)"
        return None, "task_name empty; multiple files, cannot choose"

    matches: list[tuple[str, str, int | None]] = []
    for directory, fname, mtime in file_list:
        norm_f = _normalize_filename(fname)
        if norm_f == norm_task:
            matches.append((directory, fname, mtime))

    if not matches:
        return None, f"No file matching task_name '{task_name}' (normalized: '{norm_task}')"
    if len(matches) == 1:
        d, f = matches[0][0], matches[0][1]
        path = (d.rstrip("/") + "/" + f).lstrip("/")
        return "/" + path, "Matched by task_name"

    matches_sorted = sorted(matches, key=lambda x: (x[2] is None, -(x[2] or 0)))
    d, f = matches_sorted[0][0], matches_sorted[0][1]
    path = (d.rstrip("/") + "/" + f).lstrip("/")
    return "/" + path, "Matched by task_name (newest)"


def download_3mf(
    host: str,
    password: str,
    remote_path: str,
    local_dir: str | Path | None = None,
) -> tuple[str | None, str]:
    """
    Download remote_path to a local temp file (or local_dir if provided).
    Returns (local_path, note). local_path None on failure.
    """
    local_dir = Path(local_dir) if local_dir else Path(tempfile.gettempdir())
    local_dir.mkdir(parents=True, exist_ok=True)
    base = os.path.basename(remote_path.split("?")[0])
    local_path = local_dir / base
    if _curl_ftps_download(host, password, remote_path, str(local_path)):
        return str(local_path), "Downloaded"
    return None, "Download failed (curl error or empty file)"
