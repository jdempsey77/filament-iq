#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Dict, List, Tuple

try:
    import yaml  # type: ignore
except Exception:
    print("ERROR: PyYAML is required (pip install pyyaml).", file=sys.stderr)
    raise

# Reloadable targets (and ONLY these) for LIGHT_DEPLOY.
RELOADABLE_FILES = {"automations.yaml", "scripts.yaml", "configuration.yaml"}

# Pushable without restart: SCP only, no HA reload/restart. User refreshes browser.
PUSHABLE_FILES = {"dashboards/dashboard.stage.yaml"}

# If configuration.yaml changes outside homeassistant.customize/customize_glob,
# LIGHT_DEPLOY must refuse (restart required).
ALLOWED_CUSTOMIZE_KEYS = {"customize", "customize_glob"}


def run(cmd: List[str]) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{p.stderr.strip()}")
    return p.stdout


def git_show(ref: str, path: str) -> str:
    # If file doesn't exist at ref, treat as empty (deterministic).
    try:
        return run(["git", "show", f"{ref}:{path}"])
    except Exception:
        return ""


def load_yaml(s: str) -> Any:
    if not s.strip():
        return {}
    return yaml.safe_load(s) or {}


def strip_allowed_customizations(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a copy of configuration.yaml with homeassistant.customize/customize_glob removed,
    so changes elsewhere are detected deterministically.
    """
    import copy

    d = copy.deepcopy(doc) if isinstance(doc, dict) else {}
    if not isinstance(d, dict):
        return {}

    ha = d.get("homeassistant")
    if isinstance(ha, dict):
        for k in list(ALLOWED_CUSTOMIZE_KEYS):
            ha.pop(k, None)
        if not ha:
            d.pop("homeassistant", None)

    return d


def config_diff_is_customize_only(base_ref: str) -> Tuple[bool, str]:
    base_raw = git_show(base_ref, "configuration.yaml")
    try:
        head_raw = open("configuration.yaml", "r", encoding="utf-8").read()
    except FileNotFoundError:
        # If configuration.yaml doesn't exist in working tree but exists in base,
        # that's definitely restart-required.
        return (False, "configuration.yaml missing in working tree (restart required)")

    base_doc = load_yaml(base_raw)
    head_doc = load_yaml(head_raw)

    if not isinstance(base_doc, dict) or not isinstance(head_doc, dict):
        return (False, "configuration.yaml top-level is not a mapping (refuse)")

    base_stripped = strip_allowed_customizations(base_doc)
    head_stripped = strip_allowed_customizations(head_doc)

    if base_stripped != head_stripped:
        return (False, "configuration.yaml changed outside homeassistant.customize/customize_glob (restart required)")

    return (True, "configuration.yaml diff is customize-only")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", required=True, help="git ref to diff against (e.g. origin/main)")
    ap.add_argument("--changed-files-json", required=True, help="JSON array of changed files")
    args = ap.parse_args()

    changed_files: List[str] = json.loads(args.changed_files_json)
    changed_files = [f.strip() for f in changed_files if f and f.strip()]

    reloadables: List[str] = []
    requires_restart: List[str] = []
    reasons: List[str] = []

    for f in changed_files:
        if f in ("automations.yaml", "scripts.yaml"):
            reloadables.append(f)
            continue

        if f == "configuration.yaml":
            ok, msg = config_diff_is_customize_only(args.base_ref)
            if ok:
                reloadables.append(f)
                reasons.append(msg)
            else:
                requires_restart.append(f)
                reasons.append(msg)
            continue

        if f in PUSHABLE_FILES:
            reloadables.append(f)  # Reuse reloadables for "deployable without restart"
            reasons.append(f"{f}: pushable (SCP only, no restart; refresh browser)")
            continue

        # Deterministic stance: anything else is NOT reloadable for LIGHT_DEPLOY.
        requires_restart.append(f)
        reasons.append(f"{f}: not reloadable for LIGHT_DEPLOY (restart required)")

    out = {
        "changed_files": changed_files,
        "reloadables": sorted(set(reloadables)),
        "requires_restart": sorted(set(requires_restart)),
        "reasons": reasons,
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
