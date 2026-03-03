#!/usr/bin/env python3
"""
Validate configuration.yaml for common mistakes.
Run before deploying: python3 scripts/validate_config.py
"""
import re
import sys
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "configuration.yaml"


def _include_constructor(loader, node):
    """Placeholder for !include - HA-specific, we skip for validation."""
    return "<<placeholder>>"


def _include_dir_constructor(loader, node):
    """Placeholder for !include_dir_merge_named - HA-specific."""
    return "<<placeholder>>"


yaml.add_constructor("!include", _include_constructor, yaml.SafeLoader)
yaml.add_constructor("!include_dir_merge_named", _include_dir_constructor, yaml.SafeLoader)

VALID_INPUT_BOOLEAN_KEYS = {"name", "icon", "initial"}
VALID_INPUT_TEXT_KEYS = {"name", "icon", "initial", "min", "max", "pattern", "mode"}
VALID_INPUT_NUMBER_KEYS = {"name", "icon", "initial", "min", "max", "step", "mode", "unit_of_measurement"}
VALID_INPUT_SELECT_KEYS = {"name", "icon", "initial", "options"}
VALID_INPUT_BUTTON_KEYS = {"name", "icon"}

SECTION_VALIDATORS = {
    "input_boolean": VALID_INPUT_BOOLEAN_KEYS,
    "input_text": VALID_INPUT_TEXT_KEYS,
    "input_number": VALID_INPUT_NUMBER_KEYS,
    "input_select": VALID_INPUT_SELECT_KEYS,
    "input_button": VALID_INPUT_BUTTON_KEYS,
}


def validate_config():
    errors = []

    if not CONFIG_PATH.exists():
        print(f"ERROR: {CONFIG_PATH} not found")
        sys.exit(1)

    with open(CONFIG_PATH, "r") as f:
        try:
            config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"ERROR: YAML parse error: {e}")
            sys.exit(1)

    if not isinstance(config, dict):
        print("ERROR: configuration.yaml root is not a dict")
        sys.exit(1)

    # Check each input_* section for invalid keys
    for section_name, valid_keys in SECTION_VALIDATORS.items():
        section = config.get(section_name)
        if not isinstance(section, dict):
            continue
        for entity_id, entity_config in section.items():
            if not isinstance(entity_config, dict):
                continue
            invalid_keys = set(entity_config.keys()) - valid_keys
            if invalid_keys:
                errors.append(
                    f"{section_name}.{entity_id}: invalid keys {invalid_keys} "
                    f"(valid: {valid_keys})"
                )
            # Check initial value type for input_boolean
            if section_name == "input_boolean":
                initial = entity_config.get("initial")
                if initial is not None and not isinstance(initial, bool):
                    errors.append(
                        f"{section_name}.{entity_id}: 'initial' must be boolean, "
                        f"got {type(initial).__name__} ({initial!r})"
                    )

    # Cross-reference: check scripts.yaml and automations.yaml for entity references
    # that don't match their section in configuration.yaml
    for yaml_file in ["scripts.yaml", "automations.yaml"]:
        path = REPO_ROOT / yaml_file
        if not path.exists():
            continue
        content = path.read_text()
        for section_name in SECTION_VALIDATORS:
            prefix = section_name + "."
            pattern = rf"{re.escape(prefix)}(\w+)"
            for match in re.finditer(pattern, content):
                entity_id = match.group(1)
                section = config.get(section_name)
                if isinstance(section, dict) and entity_id in section:
                    continue
                # Check if it's in a DIFFERENT input_* section (misplaced)
                for other_section in SECTION_VALIDATORS:
                    if other_section == section_name:
                        continue
                    other = config.get(other_section)
                    if isinstance(other, dict) and entity_id in other:
                        errors.append(
                            f"{yaml_file} references {prefix}{entity_id} but it's "
                            f"defined under {other_section}: (should be {section_name}:)"
                        )
                        break

    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s):\n")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("✓ configuration.yaml validation passed")
        sys.exit(0)


if __name__ == "__main__":
    validate_config()
