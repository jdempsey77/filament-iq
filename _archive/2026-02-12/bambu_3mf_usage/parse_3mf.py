"""
Parse Bambu 3MF (ZIP): Metadata/slice_info.config is XML.
Extract filaments: id, type (material), color (hex), used_g, used_m, tray_info_idx.
Color is normalized to 6 hex chars (rrggbb); 8-char RRGGBBAA drops alpha.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path


def normalize_color_hex(h: str | None) -> str:
    """
    Single place for color normalization. Strip '#', lowercase.
    If 8 hex chars (RRGGBBAA), drop AA => 6 chars (rrggbb).
    If 6 hex chars, keep. Output always 6 hex chars or empty.
    """
    if not h or not isinstance(h, str):
        return ""
    h = h.strip().lower().lstrip("#")
    h = re.sub(r"[^0-9a-f]", "", h)
    if len(h) == 8:
        return h[:6]
    if len(h) == 6:
        return h
    return h[:6] if len(h) >= 6 else h


@dataclass
class FilamentUsage:
    """One filament entry from the 3MF."""
    used_g: float | None = None
    used_m: float | None = None
    color_hex: str | None = None
    material: str | None = None
    index: int = 0
    id: str | None = None
    tray_info_idx: str | None = None


# Verified on real P1S 3MF: Metadata/slice_info.config (INI), Metadata/filament_sequence.json
SLICE_INFO_CONFIG = "Metadata/slice_info.config"
FILAMENT_SEQUENCE_JSON = "Metadata/filament_sequence.json"


def _read_member(zip_path: Path, member: str) -> bytes | None:
    """Read a single member from the ZIP; returns None if missing."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        if member not in zf.namelist():
            return None
        return zf.read(member)


def _parse_ini_style(content: bytes) -> list[dict]:
    """Parse INI-style or key=value lines into list of section dicts."""
    text = content.decode("utf-8", errors="replace")
    sections: list[dict] = []
    current: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            if current:
                sections.append(current)
            current = {}
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()
    if current:
        sections.append(current)
    return sections


def _parse_json_filament(content: bytes) -> list[FilamentUsage]:
    """Try to extract filament usage from JSON (plate_*.json style)."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    out: list[FilamentUsage] = []
    # Possible shapes: {"filaments": [...]}, or top-level list, or key per filament
    filaments = data.get("filaments") or data.get("filament_usage") or data.get("filament_infos")
    if isinstance(filaments, list):
        for i, f in enumerate(filaments):
            if not isinstance(f, dict):
                continue
            used_g = f.get("used_g") or f.get("used_grams")
            used_m = f.get("used_m") or f.get("used_mm") or f.get("used_length_mm")
            if used_g is not None:
                used_g = float(used_g)
            if used_m is not None:
                used_m = float(used_m)
            raw_color = f.get("color_hex") or f.get("color") or f.get("hex_color")
            if isinstance(raw_color, dict):
                raw_color = raw_color.get("hex") or raw_color.get("value")
            color_hex = normalize_color_hex(str(raw_color)) if raw_color else None
            material = f.get("material") or f.get("filament_type") or f.get("type")
            out.append(
                FilamentUsage(
                    used_g=used_g,
                    used_m=float(used_m) if used_m is not None else None,
                    color_hex=color_hex or None,
                    material=str(material).strip() if material else None,
                    index=i,
                )
            )
    return out


def _parse_xml_filament(content: bytes) -> list[FilamentUsage]:
    """
    Parse Metadata/slice_info.config XML.
    Extract <filament id="..." type="..." color="#RRGGBBAA" used_g="..." used_m="..." tray_info_idx="..."/>.
    Color is normalized to 6 hex chars (drop alpha if 8).
    """
    out: list[FilamentUsage] = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    for i, elem in enumerate(root.iter("filament")):
        if elem.tag != "filament":
            continue
        used_g_s = elem.get("used_g")
        used_m_s = elem.get("used_m")
        if used_g_s is None and used_m_s is None:
            continue
        try:
            used_g = float(used_g_s) if used_g_s is not None else None
        except (TypeError, ValueError):
            used_g = None
        try:
            used_m = float(used_m_s) if used_m_s is not None else None
        except (TypeError, ValueError):
            used_m = None
        raw_color = elem.get("color")
        color_hex = normalize_color_hex(raw_color) if raw_color else None
        material = elem.get("type")
        material = material.strip() if material else None
        out.append(
            FilamentUsage(
                used_g=used_g,
                used_m=used_m,
                color_hex=color_hex or None,
                material=material,
                index=i,
                id=elem.get("id"),
                tray_info_idx=elem.get("tray_info_idx"),
            )
        )
    return out


def _parse_config_filament(content: bytes) -> list[FilamentUsage]:
    """Parse slice_info.config INI style: [filament_0], ... or filament_0_used_g=... (fallback)."""
    sections = _parse_ini_style(content)
    out: list[FilamentUsage] = []
    for s in sections:
        used_g = s.get("used_g") or s.get("used_grams")
        used_m = s.get("used_m") or s.get("used_mm") or s.get("used_length_mm")
        raw_color = s.get("color_hex") or s.get("color") or s.get("hex")
        color_hex = normalize_color_hex(str(raw_color)) if raw_color else None
        material = s.get("material") or s.get("filament_type") or s.get("type")
        if used_g is None and used_m is None:
            continue
        out.append(
            FilamentUsage(
                used_g=float(used_g) if used_g is not None else None,
                used_m=float(used_m) if used_m is not None else None,
                color_hex=color_hex or None,
                material=str(material).strip() if material else None,
                index=len(out),
            )
        )
    # Also try single-section with filament_0_used_g, filament_1_used_g, ...
    if not out:
        flat: dict[str, str] = {}
        for s in sections:
            flat.update(s)
        idx = 0
        while True:
            ug = flat.get(f"filament_{idx}_used_g") or flat.get(f"filament_{idx}_used_grams")
            um = flat.get(f"filament_{idx}_used_m") or flat.get(f"filament_{idx}_used_mm")
            if ug is None and um is None:
                break
            raw_color = flat.get(f"filament_{idx}_color_hex") or flat.get(f"filament_{idx}_color")
            color_hex = normalize_color_hex(str(raw_color)) if raw_color else None
            mat = flat.get(f"filament_{idx}_material") or flat.get(f"filament_{idx}_type")
            out.append(
                FilamentUsage(
                    used_g=float(ug) if ug is not None else None,
                    used_m=float(um) if um is not None else None,
                    color_hex=color_hex or None,
                    material=str(mat).strip() if mat else None,
                    index=idx,
                )
            )
            idx += 1
    return out


def _parse_filament_sequence(content: bytes) -> list[int] | None:
    """Parse filament_sequence.json: array of filament indices (print order). Returns None if invalid."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    order: list[int] = []
    for x in data:
        try:
            order.append(int(x))
        except (TypeError, ValueError):
            return None
    return order if order else None


def parse_3mf(path: str | Path) -> tuple[list[FilamentUsage], list[str], list[int] | None]:
    """
    Open 3MF as ZIP; read Metadata/slice_info.config (INI) for per-filament usage;
    optionally Metadata/filament_sequence.json for print order.
    Returns (usages, notes, filament_order).
    Prefer used_g; used_m only usable if caller provides density (no guessing).
    """
    path = Path(path)
    notes: list[str] = []
    if not path.exists():
        return [], [f"File not found: {path}"], None
    if not zipfile.is_zipfile(path):
        return [], [f"Not a ZIP/3MF: {path}"], None

    content = _read_member(path, SLICE_INFO_CONFIG)
    if not content:
        return [], ["Metadata/slice_info.config not found in 3MF"], None

    notes.append(f"Using {SLICE_INFO_CONFIG}")

    # Real Bambu slice_info.config is XML; fall back to INI-style if no <filament> elements
    if content.strip().startswith(b"<?xml") or b"<config>" in content or b"<filament " in content:
        usages = _parse_xml_filament(content)
        if usages:
            notes.append("Parsed slice_info.config as XML")
    else:
        usages = []
    if not usages:
        usages = _parse_config_filament(content)
    if not usages:
        return [], notes + ["No filament entries with used_g/used_m in slice_info.config"], None

    # Colors already normalized in _parse_xml_filament (6 hex chars)

    # Optional: filament order for tie-breaker when mapping to slots
    filament_order: list[int] | None = None
    seq_content = _read_member(path, FILAMENT_SEQUENCE_JSON)
    if seq_content:
        filament_order = _parse_filament_sequence(seq_content)
        if filament_order is not None:
            notes.append("Using filament_sequence.json for order tie-breaker")

    return usages, notes, filament_order
