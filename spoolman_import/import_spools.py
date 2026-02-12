#!/usr/bin/env python3
"""
Bulk import of spools from spools.csv into Spoolman.
Idempotent: skips rows when a spool with the same filament name already exists.
Use for ongoing sync (e.g. after adding new spools to the CSV). Python 3.11+.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

DEFAULT_CSV = "spools.csv"
DEFAULT_BASE_PATH = "/api/v1"
LOG = logging.getLogger("import_spools")

# Filament defaults when not in CSV (Spoolman API requires density and diameter)
DEFAULT_DENSITY = 1.24  # g/cm³
DEFAULT_DIAMETER = 1.75  # mm

# Retry
MAX_RETRIES = 5
INITIAL_BACKOFF = 1.0
BACKOFF_MULTIPLIER = 2.0

# CSV columns
REQUIRED_COLS = {"name", "brand", "material", "color", "status", "location", "remaining_g", "empty_spool_g", "notes"}


def _parse_args() -> argparse.Namespace:
    base_url = os.environ.get("SPOOLMAN_URL", "").rstrip("/")
    p = argparse.ArgumentParser(
        description="Bulk import spools from CSV into Spoolman (idempotent by spool name)."
    )
    p.add_argument(
        "csv_file",
        nargs="?",
        default=DEFAULT_CSV,
        help=f"Path to CSV file (default: {DEFAULT_CSV})",
    )
    p.add_argument(
        "--url",
        default=base_url,
        help="Spoolman base URL (e.g. http://host:7912). Overrides SPOOLMAN_URL.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without calling the API.",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    return p.parse_args()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(levelname)s %(message)s",
        level=level,
        stream=sys.stderr,
    )


# -----------------------------------------------------------------------------
# HTTP with retries
# -----------------------------------------------------------------------------


def _should_retry(response: requests.Response | None, exc: Exception | None) -> bool:
    if exc is not None:
        return True
    if response is None:
        return True
    return response.status_code >= 500


def _request_with_retries(
    method: str,
    url: str,
    *,
    session: requests.Session,
    json: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    dry_run: bool = False,
) -> requests.Response | None:
    if dry_run and method.upper() != "GET":
        return None
    last_exc: Exception | None = None
    last_response: requests.Response | None = None
    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            r = session.request(method, url, json=json, params=params, timeout=30)
            last_response = r
            if not _should_retry(r, None):
                return r
            last_exc = None
        except requests.RequestException as e:
            last_exc = e
        if attempt < MAX_RETRIES - 1:
            LOG.debug("Retry in %.1fs (attempt %d)", backoff, attempt + 1)
            time.sleep(backoff)
            backoff *= BACKOFF_MULTIPLIER
    if last_exc is not None:
        raise last_exc
    return last_response


# -----------------------------------------------------------------------------
# Spoolman API helpers
# -----------------------------------------------------------------------------


def _check_reachable(base_url: str, session: requests.Session, dry_run: bool) -> None:
    if dry_run:
        return
    health_url = f"{base_url}/api/v1/health"
    try:
        r = session.get(health_url, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print("Spoolman is unreachable.", file=sys.stderr)
        print(f"  URL: {health_url}", file=sys.stderr)
        print(f"  Error: {e}", file=sys.stderr)
        print("  Check SPOOLMAN_URL or --url and that Spoolman is running.", file=sys.stderr)
        sys.exit(1)


def _get_existing_spool_names(base_url: str, session: requests.Session) -> set[str]:
    """Fetch all spools and return set of filament names (for idempotency)."""
    out: set[str] = set()
    url = f"{base_url}{DEFAULT_BASE_PATH}/spool"
    params: dict[str, str] = {"limit": "1000"}
    while True:
        r = _request_with_retries("GET", url, session=session, params=params)
        if r is None or not r.ok:
            break
        data = r.json()
        if not data:
            break
        for s in data:
            fn = s.get("filament") or {}
            name = fn.get("name")
            if name is not None:
                out.add(name)
        if len(data) < 1000:
            break
        params["offset"] = str(int(params.get("offset", 0)) + len(data))
    return out


def _find_vendor_by_name(base_url: str, session: requests.Session, name: str) -> int | None:
    if not (name or name.strip()):
        return None
    url = f"{base_url}{DEFAULT_BASE_PATH}/vendor"
    r = _request_with_retries(
        "GET",
        url,
        session=session,
        params={"vendor.name": f'"{name.strip()}"'},
    )
    if r is None or not r.ok:
        return None
    data = r.json()
    if data and len(data) > 0:
        return int(data[0]["id"])
    return None


def _create_vendor(base_url: str, session: requests.Session, name: str, dry_run: bool) -> int | None:
    url = f"{base_url}{DEFAULT_BASE_PATH}/vendor"
    payload = {"name": name.strip()}
    if dry_run:
        LOG.debug("Would POST %s %s", url, payload)
        return None
    r = _request_with_retries("POST", url, session=session, json=payload)
    if r is None or not r.ok:
        return None
    return int(r.json()["id"])


def _get_or_create_vendor(base_url: str, session: requests.Session, brand: str, dry_run: bool) -> int | None:
    brand = (brand or "").strip()
    if not brand:
        return None
    vid = _find_vendor_by_name(base_url, session, brand)
    if vid is not None:
        return vid
    return _create_vendor(base_url, session, brand, dry_run)


def _color_to_hex(value: str) -> str | None:
    s = (value or "").strip()
    if not s:
        return None
    s = s.lstrip("#")
    if re.match(r"^[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$", s):
        return s
    return None


def _find_filament_by_name(base_url: str, session: requests.Session, name: str) -> int | None:
    if not (name or name.strip()):
        return None
    url = f"{base_url}{DEFAULT_BASE_PATH}/filament"
    r = _request_with_retries(
        "GET",
        url,
        session=session,
        params={"name": f'"{name.strip()}"'},
    )
    if r is None or not r.ok:
        return None
    data = r.json()
    if data and len(data) > 0:
        return int(data[0]["id"])
    return None


def _create_filament(
    base_url: str,
    session: requests.Session,
    name: str,
    vendor_id: int | None,
    material: str,
    color_hex: str | None,
    dry_run: bool,
) -> int | None:
    url = f"{base_url}{DEFAULT_BASE_PATH}/filament"
    payload: dict[str, Any] = {
        "name": name.strip(),
        "material": (material or "").strip() or None,
        "density": DEFAULT_DENSITY,
        "diameter": DEFAULT_DIAMETER,
    }
    if vendor_id is not None:
        payload["vendor_id"] = vendor_id
    if color_hex is not None:
        payload["color_hex"] = color_hex
    if dry_run:
        LOG.debug("Would POST %s %s", url, payload)
        return None
    r = _request_with_retries("POST", url, session=session, json=payload)
    if r is None or not r.ok:
        return None
    return int(r.json()["id"])


def _get_or_create_filament(
    base_url: str,
    session: requests.Session,
    row: dict[str, str],
    dry_run: bool,
) -> int | None:
    name = (row.get("name") or "").strip()
    if not name:
        return None
    fid = _find_filament_by_name(base_url, session, name)
    if fid is not None:
        return fid
    vendor_id = _get_or_create_vendor(base_url, session, row.get("brand") or "", dry_run)
    material = (row.get("material") or "").strip() or None
    color_hex = _color_to_hex(row.get("color") or "")
    return _create_filament(
        base_url,
        session,
        name,
        vendor_id,
        material or "",
        color_hex,
        dry_run,
    )


def _status_to_archived(status: str) -> bool:
    s = (status or "").strip().lower()
    return s in ("archived", "empty", "used")


def _build_spool_payload(row: dict[str, str], filament_id: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "filament_id": filament_id,
        "location": (row.get("location") or "").strip() or None,
        "comment": (row.get("notes") or "").strip() or None,
        "archived": _status_to_archived(row.get("status") or ""),
    }
    rg = (row.get("remaining_g") or "").strip()
    if rg:
        try:
            payload["remaining_weight"] = float(rg)
        except ValueError:
            pass
    es = (row.get("empty_spool_g") or "").strip()
    if es:
        try:
            payload["spool_weight"] = float(es)
        except ValueError:
            pass
    return payload


def _create_spool(
    base_url: str,
    session: requests.Session,
    payload: dict[str, Any],
    dry_run: bool,
) -> bool:
    url = f"{base_url}{DEFAULT_BASE_PATH}/spool"
    if dry_run:
        LOG.debug("Would POST %s %s", url, payload)
        return True
    r = _request_with_retries("POST", url, session=session, json=payload)
    if r is None or not r.ok:
        return False
    return True


# -----------------------------------------------------------------------------
# CSV and main
# -----------------------------------------------------------------------------


def _read_csv(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        missing = REQUIRED_COLS - set(reader.fieldnames)
        if missing:
            raise SystemExit(f"CSV missing columns: {sorted(missing)}. Expected: {sorted(REQUIRED_COLS)}")
        return list(reader)


def main() -> None:
    args = _parse_args()
    _setup_logging(args.verbose)

    base_url = (args.url or os.environ.get("SPOOLMAN_URL") or "").strip().rstrip("/")
    if not base_url and not args.dry_run:
        print("Error: Spoolman URL is required. Set SPOOLMAN_URL or use --url.", file=sys.stderr)
        sys.exit(1)
    if base_url:
        LOG.info("Spoolman URL: %s", base_url)

    csv_path = args.csv_file
    if not Path(csv_path).exists():
        print(f"Error: CSV file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    rows = _read_csv(csv_path)
    LOG.info("Loaded %d rows from %s", len(rows), csv_path)
    if not args.dry_run:
        print(f"Importing {len(rows)} rows from {csv_path}...", flush=True)

    session = requests.Session()
    session.headers["Content-Type"] = "application/json"

    if not args.dry_run and base_url:
        _check_reachable(base_url, session, args.dry_run)
    existing_names: set[str] = set()
    if base_url:
        try:
            existing_names = _get_existing_spool_names(base_url, session)
            LOG.debug("Existing spool names (by filament): %s", len(existing_names))
        except requests.RequestException as e:
            if not args.dry_run:
                raise
            LOG.warning("Could not fetch existing spools (dry-run): %s", e)

    created_count = 0
    skipped_count = 0
    errors: list[str] = []

    for i, row in enumerate(rows, start=2):
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {i}: missing name")
            continue
        if name in existing_names:
            skipped_count += 1
            LOG.debug("Skip (exists): %s", name)
            continue
        if args.dry_run:
            payload = _build_spool_payload(row, filament_id=0)
            print(f"[dry-run] Would create spool: name={name!r} filament_id=0 payload={payload}")
            created_count += 1
            continue
        filament_id = _get_or_create_filament(base_url, session, row, args.dry_run)
        if filament_id is None:
            errors.append(f"Row {i} ({name}): could not get or create filament")
            continue
        payload = _build_spool_payload(row, filament_id)
        if _create_spool(base_url, session, payload, args.dry_run):
            created_count += 1
            existing_names.add(name)
        else:
            errors.append(f"Row {i} ({name}): failed to create spool")

    # Summary
    print("Summary:", flush=True)
    print(f"  created: {created_count}", flush=True)
    print(f"  skipped (already exist): {skipped_count}", flush=True)
    print(f"  errors: {len(errors)}", flush=True)
    if errors:
        for e in errors:
            print(f"    - {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
