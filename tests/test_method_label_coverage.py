"""
Audit: every method value emitted by code in apps/filament_iq/ (both
``consumption_engine.py`` and ``ams_print_usage_sync.py``) must have a
corresponding label in ``_METHOD_LABELS``.

Analogous to ``test_consumption_engine_skip_reason_keys_match_label_map``
which guards ``_SKIP_REASON_LABELS``.
"""

import re
import sys
import types
import pathlib

# Bootstrap fake hassapi before importing module (no appdaemon dep at test time)
if "hassapi" not in sys.modules:
    _hassapi = types.ModuleType("hassapi")

    class _FakeHass:
        def __init__(self, ad=None, name=None, logger=None, args=None,
                     config=None, app_config=None, global_vars=None):
            self.args = args or {}

        def log(self, msg, level="INFO"):
            pass

    _hassapi.Hass = _FakeHass
    sys.modules["hassapi"] = _hassapi


def _read_source(rel_path: str) -> str:
    repo_root = pathlib.Path(__file__).parent.parent
    return (repo_root / rel_path).read_text(encoding="utf-8")


# Known-good method values from _METHOD_LABELS.  Any additional values found
# in source that are not in this set are flagged as unlabelled.
# We collect from two sources:
#   1. ``consumption_engine.py``: SlotDecision(method=...) keyword args
#   2. ``ams_print_usage_sync.py``: _detect_runout_split tuple writes

def test_method_labels_cover_all_emitted_methods():
    """Every method= string literal emitted by consumption_engine.py and
    the runout-split tuple writes in ams_print_usage_sync.py must appear
    as a key in _METHOD_LABELS."""
    from filament_iq.ams_print_usage_sync import _METHOD_LABELS

    found: set[str] = set()

    # -- consumption_engine.py: SlotDecision(method="value") --
    # Only match lowercase-only method values (consumption methods are all
    # lowercase with underscores; HTTP methods like PATCH/PUT are filtered out).
    ce_src = _read_source("apps/filament_iq/consumption_engine.py")
    for m in re.finditer(r'\bmethod\s*=\s*["\']([a-z0-9_]+)["\']', ce_src):
        found.add(m.group(1))

    # -- ams_print_usage_sync.py: _detect_runout_split tuple writes --
    # Pattern: ``updated[slot] = (share, "runout_split")``
    # Only collect from _RUNOUT_SPLIT_METHODS by matching the direct tuple
    # assignments inside _detect_runout_split (lines that set updated[x]).
    sync_src = _read_source("apps/filament_iq/ams_print_usage_sync.py")
    # Match assignments of the form: updated[expr] = (expr, "method_string")
    for m in re.finditer(
        r'updated\s*\[.+?\]\s*=\s*\(\s*[\w.]+\s*,\s*["\']([a-z0-9_]+)["\']\s*\)',
        sync_src,
    ):
        found.add(m.group(1))

    unmapped = found - set(_METHOD_LABELS.keys())
    assert not unmapped, (
        f"method values not in _METHOD_LABELS: {sorted(unmapped)} "
        f"— add labels in ams_print_usage_sync.py _METHOD_LABELS dict"
    )
