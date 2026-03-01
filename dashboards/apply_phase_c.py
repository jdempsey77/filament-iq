#!/usr/bin/env python3
"""
Replace the AMS slot cards block (lines 1503-2940) in dashboard.test.storage.yaml
with the Phase C design: single vertical-stack with header, grid slots 1-4,
horizontal-stack slots 5-6, and action cards.
Run from dashboards/ directory.
"""

import os
import re

# Line range (1-based inclusive); replacement is one card entry
START_LINE = 1503
END_LINE = 2940

# Indentation: parent list is "            cards:" (12 spaces), so each card is "                - " (16 spaces).
# First line of our block: "                - type: vertical-stack"
# All other lines: add 18 spaces (so "  cards:" -> "                  cards:", "    - type:" -> "                    - type:")
CARD_PREFIX = "                - "  # 16 spaces + "- "
INNER_INDENT = " " * 18  # 18 spaces


def slot_card_yaml(n: int, slot_label: str) -> str:
    """Generate mushroom-template-card YAML for one AMS slot (2-space base indent)."""
    # slot_label e.g. "Slot 1" or "Slot 5" (for HT1/HT2 we still use Slot 5 / Slot 6 in primary)
    return f"""  - type: custom:mushroom-template-card
    entity: sensor.ams_slot_{n}_name
    primary: >-
      {{% set status = states('sensor.ams_slot_{n}_status') %}}
      {{% if status == 'empty' %}}{slot_label} - Empty
      {{% elif status == 'needs_bind' %}}{slot_label} - Needs Binding
      {{% else %}}{{{{ states('sensor.ams_slot_{n}_name') }}}}
      {{% endif %}}
    secondary: >-
      {{% set status = states('sensor.ams_slot_{n}_status') %}}
      {{% if status == 'ok' %}}{{{{ states('sensor.ams_slot_{n}_vendor') }}}} - {{{{ states('sensor.ams_slot_{n}_material') }}}} - {{{{ states('sensor.ams_slot_{n}_remaining_g') }}}}g
      {{% elif status == 'needs_bind' %}}
        {{% set r = states('input_text.ams_slot_{n}_unbound_reason') %}}
        {{% if 'RFID_NOT_REFRESHED' in r %}}Unload & reload spool for RFID read
        {{% elif 'NONRFID_NO_MATCH' in r %}}No match - select spool below
        {{% elif 'AMBIGUOUS' in r %}}Multiple matches - select correct spool
        {{% elif 'GENERIC' in r or 'LOW_CONFIDENCE' in r %}}Too generic to auto-match
        {{% elif 'NOT_FOUND' in r %}}Spool missing - reassign below
        {{% elif 'UID_NO_MATCH' in r %}}RFID tag not recognized
        {{% else %}}Manual binding required
        {{% endif %}}
      {{% elif status == 'empty' %}}No spool loaded
      {{% else %}}Unknown
      {{% endif %}}
    icon: >-
      {{% set status = states('sensor.ams_slot_{n}_status') %}}
      {{% if status == 'empty' %}}mdi:tray-remove
      {{% elif status == 'needs_bind' %}}mdi:alert-circle
      {{% else %}}mdi:printer-3d-nozzle
      {{% endif %}}
    icon_color: >-
      {{% set status = states('sensor.ams_slot_{n}_status') %}}
      {{% if status == 'empty' %}}disabled
      {{% elif status == 'needs_bind' %}}red
      {{% else %}}
        {{% set hex = states('sensor.ams_slot_{n}_color_hex') %}}
        {{{{ '#' ~ hex if hex not in ['000000', 'Unknown'] else 'green' }}}}
      {{% endif %}}
    badge_icon: >-
      {{% if states('sensor.ams_slot_{n}_status') == 'needs_bind' %}}mdi:alert{{% endif %}}
    badge_color: >-
      {{% if states('sensor.ams_slot_{n}_status') == 'needs_bind' %}}red{{% endif %}}
    tap_action:
      action: more-info
    card_mod:
      style: |
        ha-card {{
          {{% set status = states('sensor.ams_slot_{n}_status') %}}
          {{% set hex = states('sensor.ams_slot_{n}_color_hex') %}}
          {{% if status == 'ok' and hex not in ['000000', 'Unknown'] %}}
          border-left: 3px solid #{{{{ hex }}}};
          --ha-card-background: #{{{{ hex }}}}15;
          {{% elif status == 'needs_bind' %}}
          border-left: 3px solid var(--error-color);
          {{% elif status == 'empty' %}}
          opacity: 0.5;
          {{% endif %}}
        }}
"""


def build_phase_c_yaml() -> str:
    """Build Phase C block with 2-space base indent (first line has no leading spaces for type)."""
    header = """  - type: custom:mushroom-template-card
    primary: "AMS Filament Slots"
    secondary: >-
      {% set count = states('sensor.ams_unbound_slot_count') | int(0) %}
      {% if count > 0 %}{{ count }} slot{{ 's' if count > 1 }} need{{ '' if count > 1 else 's' }} binding
      {% else %}All slots bound
      {% endif %}
    icon: mdi:tray-full
    icon_color: >-
      {% if states('sensor.ams_unbound_slot_count') | int(0) > 0 %}red
      {% else %}green{% endif %}
    tap_action:
      action: none
    card_mod:
      style: |
        ha-card { --ha-card-background: transparent; box-shadow: none; }
"""

    # Slot cards under grid/horizontal-stack must be indented 2 more than "cards:" (4 spaces in 2-space base)
    def indent_cards(s: str) -> str:
        return "\n".join("    " + line for line in s.split("\n"))

    grid_slots = indent_cards("".join([slot_card_yaml(i, f"Slot {i}") for i in range(1, 5)])) + "\n"
    ht_slots = indent_cards(slot_card_yaml(5, "Slot 5") + slot_card_yaml(6, "Slot 6")) + "\n"

    actions = """  - type: horizontal-stack
    cards:
      - type: custom:mushroom-template-card
        primary: Reconcile
        secondary: Run AMS slot reconciliation
        icon: mdi:sync
        icon_color: blue
        tap_action:
          action: call-service
          service: script.turn_on
          target:
            entity_id: script.reconcile_all_ams_slots
      - type: custom:mushroom-template-card
        primary: Add Spool
        secondary: Open add-spool view
        icon: mdi:plus-circle
        icon_color: green
        tap_action:
          action: navigate
          navigation_path: "#add-spool"
      - type: custom:mushroom-template-card
        primary: Spoolman
        secondary: Open Spoolman UI
        icon: mdi:database
        icon_color: orange
        tap_action:
          action: url
          url_path: "http://192.168.4.124:7912"
"""

    # Top-level: type: vertical-stack and cards: (no leading spaces on first line per spec)
    body = (
        "type: vertical-stack\n"
        "cards:\n"
        + header
        + "  - type: grid\n"
        "    columns: 2\n"
        "    square: false\n"
        "    cards:\n"
        + grid_slots
        + "  - type: horizontal-stack\n"
        "    cards:\n"
        + ht_slots
        + actions
    )
    return body


def reindent(block: str) -> str:
    """First line -> CARD_PREFIX + line; every other line -> INNER_INDENT + line (add 18 spaces)."""
    lines = block.rstrip().split("\n")
    if not lines:
        return ""
    out = [CARD_PREFIX + lines[0].strip()]
    for line in lines[1:]:
        out.append(INNER_INDENT + line)
    return "\n".join(out)


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dashboard_path = os.path.join(script_dir, "dashboard.test.storage.yaml")
    if not os.path.isfile(dashboard_path):
        raise SystemExit(f"Not found: {dashboard_path} (run from dashboards/)")

    with open(dashboard_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    phase_c = reindent(build_phase_c_yaml())
    # 1-based START_LINE, END_LINE -> replace lines[START_LINE-1:END_LINE]
    before = lines[: START_LINE - 1]
    after = lines[END_LINE:]
    new_lines = before + [phase_c + "\n"] + after

    with open(dashboard_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"Replaced lines {START_LINE}-{END_LINE} with Phase C block ({phase_c.count(chr(10)) + 1} lines)")


if __name__ == "__main__":
    main()
