"""IN-1494: filament consumption scenarios, run against the real apps through tests/harness/fiq_harness.py.

One test per tests/harness/scenarios/*.json. A scenario whose correct answer today's code gets wrong carries
an "xfail" block (reason + IN id) and runs as xfail(strict=True): when a fix makes it pass, the strict xfail
fails the suite until the block is removed, so a fix is always proven by its scenario.
"""

import glob
import json
import os
import sys

import pytest

_HARNESS = os.path.join(os.path.dirname(__file__), "harness")
if _HARNESS not in sys.path:
    sys.path.insert(0, _HARNESS)

import fiq_harness  # noqa: E402

_SCENARIOS = sorted(glob.glob(os.path.join(_HARNESS, "scenarios", "*.json")))


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _params():
    out = []
    for path in _SCENARIOS:
        sc = _load(path)
        marks = []
        if sc.get("xfail"):
            marks.append(pytest.mark.xfail(strict=True, reason=f"{sc['xfail']['in']}: {sc['xfail']['reason']}"))
        out.append(pytest.param(path, id=sc["id"], marks=marks))
    return out


def test_scenario_files_are_valid():
    assert _SCENARIOS, "no scenario files"
    for path in _SCENARIOS:
        sc = _load(path)
        assert sc["id"] == os.path.basename(path)[:-5], path
        assert sc["timeline"] and sc["expect"], path
        if sc.get("xfail"):
            assert sc["xfail"]["reason"] and sc["xfail"]["in"].startswith("IN-"), path


@pytest.mark.parametrize("path", _params())
def test_consumption_scenario(path, tmp_path, monkeypatch):
    sc = _load(path)
    h = fiq_harness.Harness(sc, tmp_path)
    res = h.run(monkeypatch)
    bad = fiq_harness.check(sc, res)
    assert not bad, (
        f"{sc['id']} ({sc['title']}):\n  " + "\n  ".join(bad)
        + f"\ncharged={res['charged']} per_print={res['per_print']} methods={res['methods']}"
        + f"\nalerts={[a['title'] for a in res['alerts']]}\n--- app log ---\n{h.log_text()}"
    )
