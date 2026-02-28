# MIGRATION ONLY — retire after all spools have lot_nr populated. See Legacy Field Cleanup task.
"""Canonical encoding/decoding for Spoolman extra fields.

Spoolman stores extra fields as JSON-in-string values. This module provides
deterministic canonicalization to prevent encoding drift (double-quoting,
stale whitespace, case variance) that causes match failures.

As of Spec v4, extra fields (rfid_tag_uid, ha_spool_uuid) are retired.
Identity now lives in lot_nr (plain string, no encoding). This module
is retained only to read legacy extra values during migration fallback.
No new write paths should use these functions.
"""

import json
import re

_HEX16_RE = re.compile(r"^[0-9A-F]{16}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_RFID_SENTINELS = frozenset({"", "0000000000000000"})


def _unwrap_json_string(raw):
    """Unwrap one layer of JSON string encoding if present.

    '"ABC"' -> 'ABC', 'plain' -> 'plain', None -> ''
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            decoded = json.loads(s)
            if decoded is None:
                return ""
            return str(decoded)
        except (json.JSONDecodeError, ValueError):
            return s
    return s


def canonicalize_rfid_tag_uid(raw):
    """Decode, strip, uppercase, validate hex-16 RFID tag UID.

    Returns the canonical uppercase hex string, or empty string for
    sentinels (empty, all-zero, bare '""' literal).
    """
    val = _unwrap_json_string(raw)
    val = val.strip().replace('"', "").replace(" ", "").upper()
    if val in _RFID_SENTINELS:
        return ""
    if not _HEX16_RE.match(val):
        return ""
    return val


def canonicalize_ha_spool_uuid(raw):
    """Decode and validate ha_spool_uuid (UUID format).

    Returns the stripped UUID string if valid, empty string otherwise.
    """
    val = _unwrap_json_string(raw)
    val = val.strip().replace('"', "").replace("\\", "").strip()
    if not val:
        return ""
    if _UUID_RE.match(val):
        return val
    return ""


def canonicalize_extra_scalar(raw):
    """Generic decode for Spoolman extra string fields.

    Unwraps one layer of JSON string encoding if present, strips whitespace.
    """
    return _unwrap_json_string(raw).strip()


def encode_extra_json_string(value):
    """Single JSON-encode a plain string for Spoolman extra field storage.

    'ABC123' -> '"ABC123"'

    Raises ValueError if the value appears to already be JSON-encoded
    (to prevent double-encoding).
    """
    s = str(value) if value is not None else ""
    if is_double_encoded(s) or (
        len(s) >= 2 and s[0] == '"' and s[-1] == '"'
    ):
        try:
            json.loads(s)
            raise ValueError(
                f"Value appears already JSON-encoded, refusing to double-encode: {s!r}"
            )
        except (json.JSONDecodeError, ValueError) as exc:
            if "double-encode" in str(exc):
                raise
    return json.dumps(s)


def is_double_encoded(raw):
    """Return True if value appears double-encoded (e.g. '"\\"ABC\\""').

    Heuristic: after one json.loads the result still looks like a JSON string
    (starts and ends with quotes).
    """
    if raw is None:
        return False
    s = str(raw).strip()
    if not s:
        return False
    if len(s) < 4:
        return False
    try:
        once = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(once, str):
        return False
    once_stripped = once.strip()
    if len(once_stripped) >= 2 and once_stripped[0] == '"' and once_stripped[-1] == '"':
        try:
            json.loads(once_stripped)
            return True
        except (json.JSONDecodeError, ValueError):
            pass
    if '\\"' in s or "\\\\" in s:
        if once_stripped.startswith('"') or once_stripped.endswith('"'):
            return True
    return False


def validate_extra_value_no_quotes(raw):
    """Decode and verify no raw quote characters remain.

    Returns the decoded value if clean.
    Raises ValueError if quotes are found after decoding.
    """
    decoded = _unwrap_json_string(raw)
    if '"' in decoded or "\\" in decoded:
        raise ValueError(
            f"Raw quote/backslash characters remain after decode: {decoded!r}"
        )
    return decoded
