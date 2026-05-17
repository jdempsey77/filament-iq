"""
Tests for the nav intent parser used by the filament-iq-manager Lovelace card.

The parseNavIntent function lives in packages/lovelace-card/src/components/SpoolsTab.jsx.
This Python re-implementation verifies the same algorithm so the behavior can be
tested in the existing pytest suite without requiring a JS runtime.

Intent format: "type:value"
Currently supported: "spool:N" where N is a positive integer Spoolman spool ID.
Reserved for future: "slot:N", "action:add".
"""

import pytest


def parse_nav_intent(intent):
    """Python mirror of parseNavIntent() in SpoolsTab.jsx."""
    if not intent:
        return None
    idx = intent.find(':')
    if idx == -1:
        return None
    type_ = intent[:idx]
    value = intent[idx + 1:]
    if type_ == 'spool' and value:
        try:
            parsed_id = int(value, 10)
        except ValueError:
            return None
        if parsed_id > 0:
            return {'type': 'spool', 'id': parsed_id}
    return None


# --- parseNavIntent unit tests ---

def test_valid_spool_intent():
    result = parse_nav_intent("spool:42")
    assert result == {'type': 'spool', 'id': 42}


def test_empty_value_returns_none():
    assert parse_nav_intent("spool:") is None


def test_non_numeric_value_returns_none():
    assert parse_nav_intent("spool:abc") is None


def test_empty_string_returns_none():
    assert parse_nav_intent("") is None


def test_none_returns_none():
    assert parse_nav_intent(None) is None


def test_reserved_slot_type_returns_none():
    # 'slot' type is not yet implemented — reserved for future use
    assert parse_nav_intent("slot:3") is None


def test_zero_spool_id_returns_none():
    # Spoolman spool IDs start at 1; 0 is an empty-slot sentinel
    assert parse_nav_intent("spool:0") is None


def test_no_colon_returns_none():
    assert parse_nav_intent("spool42") is None


def test_large_spool_id():
    result = parse_nav_intent("spool:9999")
    assert result == {'type': 'spool', 'id': 9999}


# --- Graceful degradation scenarios ---

def test_unknown_spool_id_parses_successfully():
    # Card receives editId=9999; no matching spool in list → no row expands.
    # The parser itself must not reject unknown IDs — graceful degradation is
    # the card's responsibility, not the parser's.
    result = parse_nav_intent("spool:9999")
    assert result is not None
    assert result['id'] == 9999


def test_null_intent_fires_no_service_call():
    # Simulated: when navIntent is None/null, parseNavIntent returns None,
    # so no sendMessage is called and editId stays null.
    assert parse_nav_intent(None) is None
