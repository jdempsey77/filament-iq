"""
Map parsed filament usage to AMS slots and Spoolman spool IDs.
Color comparison uses normalize_color_hex (6-char rrggbb). Material: case-insensitive, trimmed.
"""

from __future__ import annotations

from parse_3mf import FilamentUsage, normalize_color_hex


def _material_normalize(s: str | None) -> str:
    """Case-insensitive, trimmed for material matching."""
    if s is None:
        return ""
    return s.strip().lower()


def _color_match(a: str | None, b: str | None) -> bool:
    """Both sides must be normalized (6-char rrggbb). Empty means skip check."""
    na = normalize_color_hex(a) if a else ""
    nb = normalize_color_hex(b) if b else ""
    if not na or not nb:
        return True
    return na == nb


def _material_match(a: str | None, b: str | None) -> bool:
    """Material: case-insensitive, trimmed; empty matches any."""
    na, nb = _material_normalize(a or ""), _material_normalize(b or "")
    if not na or not nb:
        return True
    return na == nb


def _used_m_to_g(used_m: float, density_g_per_cm3: float) -> float:
    """Convert used_m (meters) to grams; 1.75mm filament, density in g/cm³."""
    import math
    radius_mm = 1.75 / 2.0
    length_mm = used_m * 1000.0
    volume_mm3 = length_mm * (math.pi * radius_mm * radius_mm)
    volume_cm3 = volume_mm3 * 0.001
    return volume_cm3 * density_g_per_cm3


def map_filaments_to_slots(
    usages: list[FilamentUsage],
    ams_state: dict[int, dict],  # slot (1-6) -> {color_hex, material}
    spool_map: dict[int, int],   # slot -> spool_id
    density_g_per_cm3: float | None = None,
    filament_order: list[int] | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Map each filament entry to an AMS slot (color + material); order is tie-breaker when multiple slots match.
    If only used_m is present and density_g_per_cm3 is set, derive used_g (1.75mm filament). Otherwise skip (no guess).
    Returns (matches, notes). Do not guess; do not even-split.
    """
    notes: list[str] = []
    matches: list[dict] = []

    if not usages:
        return [], ["No filament usages to map"]

    # Apply filament_order tie-breaker: process usages in print order when available
    if filament_order is not None and len(filament_order) == len(usages):
        idx_to_usage = {u.index: u for u in usages}
        ordered = [idx_to_usage[i] for i in filament_order if i in idx_to_usage]
        if len(ordered) == len(usages):
            usages = ordered

    # Build list of (slot, color_hex_normalized, material_normalized) for slots that have a spool_id
    slot_info: list[tuple[int, str, str]] = []
    for slot in range(1, 7):
        spool_id = spool_map.get(slot)
        if spool_id is None:
            continue
        info = ams_state.get(slot) or {}
        raw_color = info.get("color_hex") or info.get("color") or ""
        color = normalize_color_hex(raw_color) if raw_color else ""
        material = _material_normalize(info.get("material") or info.get("type") or "")
        slot_info.append((slot, color, material))

    if not slot_info:
        return [], ["No AMS slots with spool_id mapping"]

    # Primary: color + material match; tie-breaker: order (first usage gets first matching slot)
    used_slots: set[int] = set()
    for u in usages:
        used_g = u.used_g
        if used_g is None and u.used_m is not None and density_g_per_cm3 is not None:
            used_g = _used_m_to_g(u.used_m, density_g_per_cm3)
        if used_g is None or used_g <= 0:
            notes.append(f"Filament index {u.index}: no used_g and no density to convert used_m; skipped")
            continue

        candidates = []
        for slot, s_color, s_material in slot_info:
            if slot in used_slots:
                continue
            if not _color_match(u.color_hex, s_color):
                continue
            if not _material_match(u.material, s_material):
                continue
            candidates.append(slot)

        if len(candidates) == 0:
            candidates_repr = [(s, c, m) for s, c, m in slot_info]
            notes.append(
                f"Filament index {u.index} (color={u.color_hex or ''}, material={u.material or ''}): no matching slot. "
                f"Candidates: {candidates_repr}"
            )
            continue
        if len(candidates) > 1:
            # Secondary: preserve filament order — use first matching slot still available
            slot = candidates[0]
        else:
            slot = candidates[0]
        used_slots.add(slot)
        spool_id = spool_map.get(slot)
        if spool_id is not None:
            matches.append({"slot": slot, "spool_id": int(spool_id), "used_g": round(used_g, 2)})

    return matches, notes
