# Spool list template – find real Spoolman entities

Run these in **Developer Tools → Template** and use the results to confirm entity IDs and attributes.

## 1) Integration entities (Spoolman)

```jinja2
{{ integration_entities('spoolman') }}
```

- If this returns a list of entity IDs, that’s the real Spoolman set.
- If it errors or is empty, the options sensor uses `states.sensor` filtered by `'spoolman' in entity_id` instead.

## 2) Sensor entity IDs containing "spoolman"

```jinja2
{{ states.sensor | map(attribute='entity_id') | select('search', 'spoolman') | list }}
```

If `select('search', ...)` isn’t available, use this (list of entity_id strings; search the output for “spoolman”):

```jinja2
{% set out = [] %}
{% for s in states.sensor %}{% if 'spoolman' in (s.entity_id or '') %}{{ out.append(s.entity_id) or '' }}{% endif %}{% endfor %}
{{ out }}
```

Or simply inspect one Spoolman sensor in **Developer Tools → States** (e.g. search `spoolman`) and note:

- Its `entity_id`
- Which attributes exist (e.g. `id`, `spool_id`, `filament_name`, `name`, `friendly_name`)

The options sensor uses only **string checks** (`'spoolman' in entity_id`) and **attributes** `id` / `spool_id` for the numeric ID and `filament_name` / `name` / `friendly_name` for the label. No regex and no hardcoded entity_id pattern.
