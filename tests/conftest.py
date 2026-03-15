import os
import sys

import pytest

_APPS = os.path.join(os.path.dirname(__file__), "..", "appdaemon", "apps")
_FILAMENT_IQ = os.path.join(_APPS, "filament_iq")

collect_ignore_glob = []
if not os.path.isdir(_FILAMENT_IQ):
    collect_ignore_glob.append("test_ams_*.py")
    collect_ignore_glob.append("test_threemf_*.py")
    collect_ignore_glob.append("test_consumption_engine.py")


class SpoolmanRecorder:
    """
    Records Spoolman API calls for assertion in integration tests.
    Use instead of log string matching for write verification.

    Inject into app:
        app._spoolman_use = recorder.use
        app._spoolman_patch = recorder.patch

    Then assert:
        recorder.assert_used(spool_id=10, weight=100.0)
        recorder.assert_patched_location(spool_id=10, location="Empty")
    """

    def __init__(self):
        self.use_calls = []
        self.patch_calls = []
        self._use_responses = {}

    def set_use_response(self, spool_id: int, remaining: float):
        """Configure what /use returns for a given spool_id."""
        self._use_responses[spool_id] = {
            "remaining_weight": remaining,
            "id": spool_id,
        }

    def use(self, spool_id: int, use_weight: float) -> dict:
        response = self._use_responses.get(
            spool_id,
            {"remaining_weight": 500.0, "id": spool_id},
        )
        self.use_calls.append({
            "spool_id": spool_id,
            "use_weight": use_weight,
            "response": dict(response),
        })
        return dict(response)

    def patch(self, spool_id: int, payload: dict) -> dict:
        self.patch_calls.append({"spool_id": spool_id, "payload": payload})
        return {"id": spool_id, **payload}

    def assert_used(self, spool_id: int, weight: float, tolerance: float = 0.1):
        calls = [c for c in self.use_calls if c["spool_id"] == spool_id]
        assert calls, (
            f"No /use call for spool_id={spool_id}. "
            f"All calls: {self.use_calls}"
        )
        assert abs(calls[0]["use_weight"] - weight) <= tolerance, (
            f"spool_id={spool_id}: expected ~{weight}g, "
            f"got {calls[0]['use_weight']}g"
        )

    def assert_not_used(self, spool_id: int):
        calls = [c for c in self.use_calls if c["spool_id"] == spool_id]
        assert not calls, (
            f"Unexpected /use call for spool_id={spool_id}"
        )

    def assert_patched_location(self, spool_id: int, location: str):
        calls = [
            c for c in self.patch_calls
            if c["spool_id"] == spool_id
            and c["payload"].get("location") == location
        ]
        assert calls, (
            f"No location PATCH '{location}' for spool_id={spool_id}. "
            f"All patches: {self.patch_calls}"
        )

    def assert_no_location_patch(self, spool_id: int):
        calls = [
            c for c in self.patch_calls
            if c["spool_id"] == spool_id
            and "location" in c["payload"]
        ]
        assert not calls, (
            f"Unexpected location PATCH for spool_id={spool_id}: {calls}"
        )

    @property
    def use_count(self) -> int:
        return len(self.use_calls)

    @property
    def patch_count(self) -> int:
        return len(self.patch_calls)


@pytest.fixture
def spoolman_recorder():
    return SpoolmanRecorder()
