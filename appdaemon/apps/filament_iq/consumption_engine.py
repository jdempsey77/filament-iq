"""
consumption_engine.py — Pure filament consumption decision engine.

Accepts fully-resolved SlotInput objects (all HA/Spoolman I/O already done)
and returns SlotDecision objects. No I/O, no side effects, no AppDaemon.

Decision tree per slot:

  RFID spool:
    tray_empty OR end_g == 0  → rfid_delta_depleted  (use start_g, confidence=high)
    end_g available and > 0   → rfid_delta            (use start_g - end_g, confidence=high)
    end_g is None, not empty  → no_evidence           (DATA_LOSS: end snapshot unavailable)

  Non-RFID spool:
    threemf_used_g + not empty  → 3mf                 (use threemf_used_g, confidence varies)
    threemf_used_g + tray_empty → 3mf_depleted        (use max(threemf_used_g,
                                                        spoolman_remaining), confidence=medium)
    no threemf + tray_empty     → depleted_nonrfid    (use spoolman_remaining, confidence=low)
    no threemf + not empty      → no_evidence         (nothing reliable available)

Confidence levels:
  high:   rfid_delta, rfid_delta_depleted, 3mf with exact_color_material match
  medium: 3mf with close_color/lot_nr/slot_position match, 3mf_depleted
  low:    depleted_nonrfid
  none:   no_evidence
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SlotInput:
    slot: int
    spool_id: int
    is_rfid: bool
    tray_empty: bool
    tray_active_seconds: float
    start_g: float | None
    end_g: float | None
    threemf_used_g: float | None
    threemf_method: str | None
    spoolman_remaining: float | None


@dataclass
class SlotDecision:
    slot: int
    spool_id: int
    consumption_g: float
    method: str
    skip_reason: str | None
    confidence: str
    post_write_remaining: float | None = field(default=None)
    depleted: bool = field(default=False)


def decide_consumption(
    inputs: list[SlotInput],
    min_consumption_g: float = 2.0,
    max_consumption_g: float = 1000.0,
) -> list[SlotDecision]:
    """
    Apply the decision tree to each slot input.
    Returns one SlotDecision per SlotInput, including no_evidence slots.
    Caller uses method == "no_evidence" to identify slots to skip.
    Output order matches input order.
    """
    return [_decide_slot(inp, min_consumption_g, max_consumption_g) for inp in inputs]


def _decide_slot(inp: SlotInput, min_g: float, max_g: float) -> SlotDecision:
    if inp.is_rfid:
        decision = _decide_rfid(inp)
    else:
        decision = _decide_nonrfid(inp)

    # Sanity gates
    _METHODS_EXEMPT_FROM_MIN = frozenset({"3mf", "3mf_depleted"})

    if decision.method != "no_evidence":
        if decision.consumption_g > max_g:
            return SlotDecision(
                slot=inp.slot,
                spool_id=inp.spool_id,
                consumption_g=decision.consumption_g,
                method="no_evidence",
                skip_reason=f"SANITY_CAP: {decision.consumption_g:.1f}g > max {max_g:.1f}g",
                confidence="none",
            )
        if (
            decision.consumption_g < min_g
            and decision.method not in _METHODS_EXEMPT_FROM_MIN
        ):
            return SlotDecision(
                slot=inp.slot,
                spool_id=inp.spool_id,
                consumption_g=decision.consumption_g,
                method="no_evidence",
                skip_reason=f"BELOW_MIN: {decision.consumption_g:.2f}g < min {min_g:.2f}g",
                confidence="none",
            )

    return decision


def _decide_rfid(inp: SlotInput) -> SlotDecision:
    if inp.start_g is None:
        return SlotDecision(
            slot=inp.slot,
            spool_id=inp.spool_id,
            consumption_g=0.0,
            method="no_evidence",
            skip_reason="DATA_LOSS: start_g not captured",
            confidence="none",
        )

    if inp.tray_empty or inp.end_g == 0.0:
        return SlotDecision(
            slot=inp.slot,
            spool_id=inp.spool_id,
            consumption_g=inp.start_g,
            method="rfid_delta_depleted",
            skip_reason=None,
            confidence="high",
        )

    if inp.end_g is not None and inp.end_g > 0:
        raw_delta = inp.start_g - inp.end_g
        return SlotDecision(
            slot=inp.slot,
            spool_id=inp.spool_id,
            consumption_g=max(0.0, raw_delta),
            method="rfid_delta",
            skip_reason=None,
            confidence="high",
        )

    return SlotDecision(
        slot=inp.slot,
        spool_id=inp.spool_id,
        consumption_g=0.0,
        method="no_evidence",
        skip_reason="DATA_LOSS: end_g unavailable and tray not empty",
        confidence="none",
    )


def _decide_nonrfid(inp: SlotInput) -> SlotDecision:
    if inp.threemf_used_g is not None and not inp.tray_empty:
        return SlotDecision(
            slot=inp.slot,
            spool_id=inp.spool_id,
            consumption_g=inp.threemf_used_g,
            method="3mf",
            skip_reason=None,
            confidence=_threemf_confidence(inp.threemf_method),
        )

    if inp.threemf_used_g is not None and inp.tray_empty:
        if inp.spoolman_remaining is not None:
            consumption_g = max(inp.threemf_used_g, inp.spoolman_remaining)
        else:
            consumption_g = inp.threemf_used_g
        return SlotDecision(
            slot=inp.slot,
            spool_id=inp.spool_id,
            consumption_g=consumption_g,
            method="3mf_depleted",
            skip_reason=None,
            confidence="medium",
        )

    if inp.tray_empty and inp.spoolman_remaining is not None:
        return SlotDecision(
            slot=inp.slot,
            spool_id=inp.spool_id,
            consumption_g=inp.spoolman_remaining,
            method="depleted_nonrfid",
            skip_reason=None,
            confidence="low",
        )

    return SlotDecision(
        slot=inp.slot,
        spool_id=inp.spool_id,
        consumption_g=0.0,
        method="no_evidence",
        skip_reason=_no_evidence_reason(inp),
        confidence="none",
    )


def _threemf_confidence(threemf_method: str | None) -> str:
    if threemf_method in ("exact_color_material", "single_filament_force"):
        return "high"
    if threemf_method in ("close_color_material", "lot_nr_color_material",
                          "material_only_single"):
        return "medium"
    return "low"


def _no_evidence_reason(inp: SlotInput) -> str:
    if not inp.tray_empty and inp.threemf_used_g is None:
        return "NO_3MF_AND_TRAY_NOT_EMPTY"
    if inp.tray_empty and inp.spoolman_remaining is None:
        return "DEPLETED_BUT_NO_SPOOLMAN_REMAINING"
    return "UNKNOWN"
