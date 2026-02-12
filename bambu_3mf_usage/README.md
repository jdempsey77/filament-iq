# Bambu 3MF usage — exact multi-color filament tracking

Download the sliced 3MF from a Bambu P1S (or P1/A1) via FTPS, parse per-filament `used_g` from `Metadata/slice_info.config`, map filaments to AMS slots, and output a JSON payload for Spoolman subtract (exact grams per spool). Verified on real P1S.

## What it does

1. **FTPS** — Connects to the printer (implicit TLS, port 990; user `bblp`, password = access code). Lists `*.3mf` in **`/cache/`** (P1S verified), selects file by normalized `task_name` (newest mtime tie-breaker), downloads to a local path.
2. **Parse** — Reads **`Metadata/slice_info.config`** (INI-style); extracts per-filament `used_g` (prefer) or `used_m` (only with `--density`). Optionally **`Metadata/filament_sequence.json`** for print order (tie-breaker when mapping).
3. **Map** — Maps each filament to an AMS slot by **color + material**; **order** (from filament_sequence.json when present) as tie-breaker. Outputs `matches`: `[{ "slot", "spool_id", "used_g" }, ...]`.
4. **Failure behavior** — If no 3MF found, parse fails, or mapping fails: `matches` is `[]`, `notes` explain why. No guessing; no even-split.

## Requirements

- **Python 3.9+**
- **curl** (for FTPS; must be on `PATH`)
- Printer: P1/P1S/A1 with FTPS enabled (access code from printer LCD → WiFi settings)

## Inputs (from Home Assistant)

- **Printer:** IP address, access code (same as MQTT).
- **Task name:** From `sensor.p1s_<serial>_task_name` (e.g. `Wirespool_A`).
- **AMS state JSON:** For slots 1–6, `color_hex` and `material` per slot (to match 3MF filaments). Build from HA entities, e.g. AMS tray sensors / Spoolman slot assignments.
- **Spool map JSON:** Slot → Spoolman spool ID, e.g. `{"1": 11, "2": 27, ...}` (from `input_text.ams_slot_1_spool_id`, etc.).

## CLI usage

```bash
python3 bambu_3mf_usage.py \
  --printer-ip 192.168.x.x \
  --access-code "XXXXXX" \
  --task-name "Wirespool_A" \
  --ams-json ams_state.json \
  --spoolmap-json spool_map.json \
  --out result.json
```

Optional:

- `--download-dir /path` — Where to save the downloaded 3MF (default: system temp).
- `--density 1.24` — Filament density in g/cm³; used to convert `used_m` to grams when the 3MF only has length.
- `--local-3mf /path/to/file.3mf` — Skip FTPS; parse this local 3MF file (for testing parse + map without a printer).

## Output: `result.json`

```json
{
  "matches": [
    { "slot": 1, "spool_id": 123, "used_g": 42.3 },
    { "slot": 3, "spool_id": 456, "used_g": 6.8 }
  ],
  "downloaded_file": "/cache/Wirespool_A.3mf",
  "notes": ["Matched by task_name", "Using Metadata/slice_info.config"]
}
```

When downloading from the printer, `downloaded_file` is the remote path (e.g. `/cache/Job.3mf`). With `--local-3mf` it is the local path.

On failure (no 3MF, parse error, or no mapping):

- `matches`: `[]`
- `notes`: Short reasons (e.g. "No 3MF files found on printer", "No file matching task_name '...'").

## How Home Assistant will call it

**Deployed (this repo):** Print finished runs `shell_command.bambu_3mf_after_print` → `run_after_print.py`. Wrapper gets state from HA, runs CLI, then either subtract + reload or fires `bambu_3mf_no_match` for fallback. Copy **bambu_3mf_usage** to `/config/` on the HA host; create `config.json` from `config.example.json`; install `requests`; ensure curl available.

- **Option A — shell_command:**  
  Build `ams_state.json` and `spool_map.json` in a temp dir (or under `/config/`), then run the script. Read `result.json`; if `matches` is non-empty, call `spoolman.use_spool_filament` for each entry and reload Spoolman; if empty, notify only (no subtract).

- **Option B — AppDaemon / sidecar:**  
  Same inputs/outputs; AppDaemon or a small Python service can run the script and call HA’s REST API for `spoolman.use_spool_filament` and script reload.

- **Option C — Manual test:**  
  Run the CLI from the host or a container that has network access to the printer and to HA (if calling REST). For HA, the script can run on the same machine as HA (e.g. in `/config/bambu_3mf_usage/` or in a dedicated venv).

### Example: building AMS and spool JSON for HA

- **ams_state.json** — For each slot 1–6, get color and material from the Bambu AMS tray sensor or from Spoolman (by spool ID). Example shape:

  `{"1": {"color_hex": "ff0000", "material": "PLA"}, "2": {"color_hex": "0000ff", "material": "PLA"}, ...}`

- **spool_map.json** — From HA `input_text.ams_slot_N_spool_id`:

  `{"1": "11", "2": "27", "3": "31", "4": "", "5": "", "6": ""}`  
  (empty = no spool assigned; script only includes slots with a numeric spool_id.)

## Files in this module

| File | Purpose |
|------|--------|
| `ftps_client.py` | List 3MF files via curl FTPS; pick by task_name; download to local path. |
| `parse_3mf.py` | Open 3MF as ZIP; find Metadata slice/plate file; extract per-filament used_g/used_m, color, material. |
| `map_filaments.py` | Map filament list to AMS slots (color_hex + material) and spool_id; output matches. |
| `bambu_3mf_usage.py` | CLI: wires FTPS → parse → map; writes result.json. |

## Security / gotchas

- **Access code** is secret; pass via env or HA secrets, not in logs. The script does not log the password.
- **FTPS cert:** Printer uses self-signed cert; we use `curl -k`. Only use on trusted LAN.
- **Paths:** P1S stores 3MF under **`/cache/`**; the client lists only that directory.

## Next steps after CLI works

1. Run against a real print: ensure `result.json` contains `matches` with correct `used_g` per spool.
2. Add a shell_command in HA that builds the two JSON inputs, runs the script, and parses `result.json`.
3. In the “print finished” automation: if 3MF path is used and `matches` is non-empty, loop over `matches` and call `spoolman.use_spool_filament` then reload Spoolman; if `matches` is empty, notify only (multi-color without 3MF = no subtract).
