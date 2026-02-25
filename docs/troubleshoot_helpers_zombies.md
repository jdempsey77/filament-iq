# Troubleshooting: Helpers “unavailable / restored” zombies

After a config deploy and HA restart, helpers (e.g. `input_text.spoolman_base_url`, AMS slot helpers) can appear as **restored/unavailable zombies**: `/api/states` returns HTTP 200 and entities exist, but `state` is `"unavailable"` and `attributes.restored` is `true`. Validate_helpers then reports PASS:0 and ZOMBIES:36.

---

## 1. Failure mode

- **Symptom:** `./scripts/manage_ha.sh --restart` reports Phase A and Phase B as 200, but immediately after, `./scripts/validate_helpers.sh` reports PASS:0 and ZOMBIES (restored/unavailable): 36.
- **Direct check:** `curl .../api/states/input_text.p1s_last_mapping_json` shows `state: "unavailable"` and `attributes.restored: true`.
- **Cause:** HA has returned HTTP 200 for `/api/config` and for individual entity URLs **before** the `input_text` integration has finished loading YAML-defined entities. Entities are created with `restored: true` and `state: unavailable` until the integration marks them ready. Readiness was previously “HTTP 200 only,” so we declared “ready” too early.

---

## 2. Root-cause hypotheses and decision

| Hypothesis | Description | How to confirm |
|------------|-------------|----------------|
| **A) Safe mode / partially loaded** | HA is in safe mode or core is up but integrations not fully initializing. | Check `/api/config` for `safe_mode`; `ha core info` and `ha core logs` for safe mode / errors. |
| **B) input_text not loading YAML** | Config on disk has `input_text:` but the integration is not loading entities. | On host: `grep -n '^input_text:' /config/configuration.yaml` and `grep -n 'spoolman_base_url'`; confirm entities appear in `/api/states` after full boot. |
| **C) validate_helpers misclassifying** | Helpers are healthy but script treats them as zombies. | Inspect raw JSON: `state` and `attributes.restored` for a few entities. If state is normal and restored is false, fix script logic. |
| **D) Readiness too early** | manage_ha/validate_helpers treat “HTTP 200” as ready; entities are still restored/unavailable. | **Most likely.** Fix: readiness must require `state != "unavailable"` and `attributes.restored != true` (and/or input_text count >= 30). |

**Decision:** Assume **D** and harden readiness (Phase B and settle) to require stable helpers, not just HTTP 200. If problems persist, run evidence capture and re-evaluate A/B/C.

---

## 3. Detection

- Run **evidence capture** (requires deploy.env or deploy.env.local):
  ```bash
  ./scripts/capture_helpers_evidence.sh
  ```
  Saves to `.artifacts/skill/<timestamp>/logs/`: `api_config.json`, `state_*.json`, `api_states.json`, and (if `HA_SSH_HOST` set) `ha_core_info.txt`, `ha_core_logs_grep.txt`, `config_grep.txt`.

- **Helpers health one-liner** (jq required; set `BASE` and `AUTH` or source deploy.env):
  ```bash
  curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/states" | jq '
    [.[] | select(.entity_id | startswith("input_text."))] |
    { total: length,
      unavailable: [.[] | select(.state == "unavailable")] | length,
      restored: [.[] | select(.attributes.restored == true)] | length,
      sample_unavailable: [.[] | select(.state == "unavailable") | .entity_id] | .[0:5],
      sample_restored: [.[] | select(.attributes.restored == true) | .entity_id] | .[0:5] }'
  ```

---

## 4. Recovery sequence

1. **Deploy config** (so HA has correct `input_text` and helpers on disk):
   ```bash
   ./scripts/manage_ha.sh --config
   ```
2. **Restart HA**:
   ```bash
   ./scripts/manage_ha.sh --restart
   ```
3. **Wait for readiness** (handled by manage_ha Phase B): script now waits until helpers are stable (two known helpers non-unavailable and not restored, or input_text count >= 30) or timeout.
4. **Verify** (only after readiness):
   ```bash
   ./scripts/validate_helpers.sh
   ```
   If you still see ZOMBIES, run `./scripts/capture_helpers_evidence.sh` and inspect `.artifacts/skill/<ts>/logs/`; re-check hypotheses A/B/C.

---

## 5. Guardrails in place

- **manage_ha.sh Phase B:** Requires `/api/states` and (with jq) either input_text count >= 30 **or** both `input_text.spoolman_base_url` and `input_text.ams_slot_1_spool_id` with `state != "unavailable"` and `attributes.restored != true`. Timeout: `HA_WAIT_SECONDS` (default 180).
- **validate_helpers.sh settle:** Before validation, waits for `input_text.spoolman_base_url` to have `state != "unavailable"` and `attributes.restored != true` (or timeout with WARN). No longer treats “HTTP 200 only” as ready.

---

## 6. Root cause: input_text integration not loading (has_input_text=false)

When `/api/config` shows `components.has_input_text=false` and `/api/services` has **no** `input_text` domain (so `input_text.set_value` is missing), entities can still exist in the registry (e.g. `input_text_count=95`) but all are `restored: true` and `state: unavailable` (zombies). Automations then fail with “Action input_text.set_value not found.”

**Why this happens:** Home Assistant’s startup has a “wrap-up” phase. The log message *“Something is blocking Home Assistant from wrapping up the start up phase. We're going to continue anyway.”* means some startup tasks did not complete. Pending tasks (e.g. Google Assistant `report_state` / `sync_google`) can block wrap-up. When HA continues anyway, the order of component setup can leave YAML-based integrations like `input_text` never finishing their async setup: the entity registry is populated from YAML, but the integration never registers its services, so entities stay restored/unavailable and the `input_text` domain never appears in `/api/services`. So: **blocking startup wrap-up can prevent the input_text integration from completing setup**, even though `configuration.yaml` is valid and `ha core check` passes.

**Conditions:** This is nondeterministic (timing/event loop). It is more likely when GA or other integrations delay the wrap-up phase; it is **not** caused by safe_mode or recovery_mode in the reported evidence.

---

## 7. Remediation options (do not disable Google Assistant)

1. **Google Assistant: avoid blocking wrap-up (preferred)**  
   Reduce the chance that GA blocks startup wrap-up, without disabling GA:
   - In GA integration or `configuration.yaml`, if there are options for **report_state** or **sync** behavior, set timeouts or make them non-blocking (e.g. “sync after startup” or increase timeouts) so HA can finish wrap-up.
   - Check HA/GA docs for “report_state” / “sync_google” and startup; adjust only GA-related config, not other integrations.

2. **Retry restart**  
   If input_text still does not load after one restart, run `./scripts/manage_ha.sh --restart` again. The guardrails (Phase B + validate_helpers) will not declare “ready” until `input_text` service exists and helpers are stable (or timeout); use that to decide when to retry.

3. **Watchdog / guardrails (implemented)**  
   Phase B now requires `/api/services` to include the `input_text` domain and `set_value`, plus helper stability and a 5s stability recheck. validate_helpers treats PASS:0 with required helpers as a hard FAIL with an explicit reason. So TEST and deploy will not declare success when input_text is not loaded; manual or repeated restart is required until HA starts cleanly.

---

## 8. Manual test (reproducible)

After a restart, verify input_text is loaded and helpers are not zombies:

1. **input_text service present:**
   ```bash
   curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/services" | jq 'has("input_text") and (.["input_text"] | has("set_value"))'
   ```
   Expect: `true`.

2. **Representative helpers not restored/unavailable:**
   ```bash
   curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/states/input_text.spoolman_base_url" | jq '{state, restored: .attributes.restored}'
   ```
   Expect: `state` not `"unavailable"`, `restored` not `true`.

3. **Run repo checks:**
   ```bash
   ./scripts/validate_helpers.sh
   ./scripts/skill_test.sh   # with clean tree
   ```
