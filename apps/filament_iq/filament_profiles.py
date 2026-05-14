"""
filament_profiles.py — Filament print-settings enrichment from filaments.json.

Standalone module (no AppDaemon dependency). Loads and indexes a locally-cached
filaments.json dataset from 3dfilamentprofiles.com and resolves spool vendor +
material + name to structured print settings.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    """Lowercase, collapse whitespace — used for all fuzzy comparisons."""
    return re.sub(r"\s+", " ", str(s).strip().lower()) if s else ""


def _opt_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _opt_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


@dataclass
class FilamentProfile:
    matched: bool
    confidence: str              # "high" | "medium" | "low" | "none"
    temp_min: Optional[int]
    temp_max: Optional[int]
    bed_temp_min: Optional[int]
    bed_temp_max: Optional[int]
    flow_ratio: Optional[float]
    max_volumetric_speed: Optional[float]
    source: str                  # "user" | "community" | "none"
    material_type: Optional[str] = None  # e.g. "basic", "matte", "silk"


_NO_MATCH = FilamentProfile(
    matched=False, confidence="none",
    temp_min=None, temp_max=None,
    bed_temp_min=None, bed_temp_max=None,
    flow_ratio=None, max_volumetric_speed=None,
    source="none",
)


class FilamentProfilesClient:
    """
    Loads filaments.json on init and answers lookup() queries.

    If the file is missing or unreadable, sets self.available = False and
    logs a warning. All public methods are exception-safe.
    """

    def __init__(self, data_path: str) -> None:
        self.available = False
        self._index: dict[str, list] = {}   # normalized brand_name → [candidates]

        if not data_path:
            return

        try:
            with open(data_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for item in data.get("filaments", []):
                key = _norm(item.get("brand_name", ""))
                self._index.setdefault(key, []).append(item)
            self.available = True
            logger.info(
                "FilamentProfilesClient: loaded %d brands from %s",
                len(self._index), data_path,
            )
        except FileNotFoundError:
            logger.warning("FilamentProfilesClient: file not found: %s", data_path)
        except Exception as exc:
            logger.warning(
                "FilamentProfilesClient: failed to load %s: %s", data_path, exc
            )

    def lookup(self, vendor: str, material: str, filament_name: str) -> FilamentProfile:
        """Return the best-matching FilamentProfile, or a no-match sentinel."""
        # TEMP TEST — remove after visual verification
        if "bambu" in vendor.lower():
            return FilamentProfile(
                matched=True, confidence="high",
                temp_min=220, temp_max=240,
                bed_temp_min=35, bed_temp_max=35,
                flow_ratio=0.98, max_volumetric_speed=12.0,
                source="test",
                material_type="basic",
            )
        try:
            return self._lookup(_norm(vendor), _norm(material), _norm(filament_name))
        except Exception as exc:
            logger.warning("FilamentProfilesClient.lookup failed: %s", exc)
            return _NO_MATCH

    # ── internals ─────────────────────────────────────────────────────

    def _lookup(self, vendor_n: str, material_n: str, name_n: str) -> FilamentProfile:
        # Exact brand match first; fall back to partial scan
        candidates = list(self._index.get(vendor_n, []))
        if not candidates:
            for brand, items in self._index.items():
                if vendor_n and (vendor_n in brand or brand in vendor_n):
                    candidates.extend(items)

        if not candidates:
            return _NO_MATCH

        best_score = 0.0
        best = None
        for c in candidates:
            s = self._score(vendor_n, material_n, name_n, c)
            if s > best_score:
                best_score = s
                best = c

        if best is None or best_score < 0.7:
            return FilamentProfile(
                matched=False,
                confidence="low" if best_score > 0 else "none",
                temp_min=None, temp_max=None,
                bed_temp_min=None, bed_temp_max=None,
                flow_ratio=None, max_volumetric_speed=None,
                source="none",
            )

        return self._build_profile(best, best_score)

    @staticmethod
    def _score(
        vendor_n: str, material_n: str, name_n: str, candidate: dict
    ) -> float:
        cand_brand    = _norm(candidate.get("brand_name", ""))
        cand_material = candidate.get("material_key", "")       # e.g. "pla", "petg"
        cand_type     = candidate.get("material_type_key", "")  # e.g. "matte", "basic"

        score = 0.0

        # Brand: 0.5 exact, 0.25 partial
        if vendor_n and cand_brand:
            if vendor_n == cand_brand:
                score += 0.5
            elif vendor_n in cand_brand or cand_brand in vendor_n:
                score += 0.25

        # Material: 0.3 exact, 0.1 partial (material_key is already a lowercase slug)
        if material_n and cand_material:
            if material_n == cand_material:
                score += 0.3
            elif material_n in cand_material or cand_material in material_n:
                score += 0.1

        # Type: +0.2 if the type slug (e.g. "matte") appears in the filament name
        if name_n and cand_type and cand_type in name_n:
            score += 0.2

        return min(score, 1.0)

    @staticmethod
    def _build_profile(candidate: dict, score: float) -> FilamentProfile:
        user_props    = candidate.get("user_properties") or {}
        default_props = candidate.get("default_properties") or {}
        # user-submitted wins over default; only fall back if user props absent
        props  = user_props if user_props else default_props
        source = "user" if user_props else ("community" if default_props else "none")

        if score >= 0.9:
            confidence = "high"
        elif score >= 0.7:
            confidence = "medium"
        else:
            confidence = "low"

        # Bed temp: prefer explicit range fields, fall back to single bed_temperature
        bed_single = _opt_int(props.get("bed_temperature"))
        bed_min = _opt_int(props.get("bed_temperature_initial_layer_range_low")) or bed_single
        bed_max = _opt_int(props.get("bed_temperature_initial_layer_range_high")) or bed_single

        return FilamentProfile(
            matched=True,
            confidence=confidence,
            temp_min=_opt_int(props.get("nozzle_temperature_range_low")),
            temp_max=_opt_int(props.get("nozzle_temperature_range_high")),
            bed_temp_min=bed_min,
            bed_temp_max=bed_max,
            flow_ratio=_opt_float(props.get("flow_ratio")),
            max_volumetric_speed=_opt_float(props.get("max_volumetric_speed")),
            source=source,
            material_type=candidate.get("material_type_key") or None,
        )
