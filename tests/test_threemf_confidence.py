"""_threemf_confidence rates close-colour 3MF matches medium."""
import os
import sys

_APPS = os.path.join(os.path.dirname(__file__), "..", "apps")
if _APPS not in sys.path:
    sys.path.insert(0, _APPS)

import pytest

from filament_iq.consumption_engine import _threemf_confidence


@pytest.mark.parametrize("method", [
    "close_color_material(dist=12)",
    "close_color_material(dist=12.0)",
    "close_color_material",
])
def test_close_color_material_is_medium(method):
    assert _threemf_confidence(method) == "medium"


def test_other_methods_unchanged():
    assert _threemf_confidence("exact_color_material") == "high"
    assert _threemf_confidence("something_else") == "low"
    assert _threemf_confidence(None) == "low"
