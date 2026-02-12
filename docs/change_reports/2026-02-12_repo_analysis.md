# Repository Hygiene Analysis — 2026-02-12

**Branch:** `main`  
**Status:** Clean working tree (no uncommitted changes)  
**Last commit:** `a9195b7 Initial baseline before fuel gauge system`

---

## Executive Summary

This analysis identifies orphaned files, unused helpers, and obsolete documentation following the removal of:
- AppDaemon `bambu_3mf` application
- Home Assistant 3MF pipeline (shell commands, scripts, python_scripts)

**Key Findings:**
- ✅ 1 empty directory (`python_scripts/`)
- ⚠️ 1 directory with empty subdirectory (`appdaemon/apps/`)
- ⚠️ 1 unreferenced archive directory (`bambu_3mf_usage/`)
- ⚠️ 1 sensitive file in repo root (`@`)
- ⚠️ 2 docs referencing removed features
- ⚠️ 17 root-level markdown files (14 with 0 references)

---

## Repository Structure

```
/Users/jdempsey/code/home_assistant/
├── appdaemon/
│   └── apps/                           ⚠️ Empty subdirectory
├── bambu_3mf_usage/                    ⚠️ Unreferenced archive
│   ├── testdata/
│   ├── *.py (7 files)
│   ├── README.md
│   ├── requirements.txt
│   └── config.example.json
├── dashboards/
│   ├── dashboard.stage.yaml            ✅ Active
│   ├── dashboard.test.storage.yaml
│   └── README_TEST_STORAGE.md
├── docs/
│   ├── change_reports/                 NEW (this report)
│   └── 3MF_RETRIEVAL_FEASIBILITY.md    ⚠️ References removed features
├── python_scripts/                     ⚠️ Empty directory
├── scripts/
│   ├── manage_ha.sh                    ✅ Active (cleaned)
│   ├── deploy.env                      ✅ Active (gitignored)
│   └── deploy.env.example              ✅ Active
├── spoolman_import/                    ✅ Active
│   ├── *.py (7 files)
│   ├── *.csv (2 files)
│   └── *.md (2 files)
├── *.yaml (6 core HA files)            ✅ Active
├── *.md (17 root-level files)          ⚠️ 14 with 0 references
└── @                                   🚨 Sensitive file in root
```

---

## Detailed Findings

### Category A: Safe to Archive/Remove

#### 1. **Empty Directories**

| Path | Status | Recommendation |
|------|--------|----------------|
| `python_scripts/` | Empty (all 3MF helpers removed) | **Remove directory** |
| `appdaemon/apps/` | Empty subdirectory | **Remove `appdaemon/` parent directory** |

**Rationale:**
- `python_scripts/` was cleared in Phase 3.2 when `bambu_3mf_*.py` were deleted
- `appdaemon/apps/` is empty; parent `appdaemon/` only contains this empty subdirectory
- Empty directories provide no value and create confusion

**Action:** Delete both directories

---

#### 2. **Sensitive File in Root**

| File | Size | Type | Content |
|------|------|------|---------|
| `@` | 728 bytes | ASCII text | Contains SSH credentials and tokens |

**Content Preview:**
```
SSH_HOST=192.168.4.124
SSH_USER=root
HOME_ASSISTANT_URL=http://192.168.4.124:8123
HOME_ASSISTANT_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
REMOTE_APPDAEMON_APPS_PATH=/addon_configs/a0d7b954_appdaemon/apps
```

**Analysis:**
- Appears to be a duplicate or stray copy of `scripts/deploy.env`
- Contains **sensitive credentials** (SSH config, HA token)
- Not referenced anywhere in code
- **Should be in `.gitignore`** if committed

🚨 **CRITICAL:** Delete immediately and verify not in git history

**Action:** Delete `@` file

---

#### 3. **Obsolete Documentation (References Removed Features)**

| File | Size | References Removed Feature | Archive? |
|------|------|---------------------------|----------|
| `docs/3MF_RETRIEVAL_FEASIBILITY.md` | ~8KB | 3MF retrieval research for AppDaemon/wrapper | **Yes** |

**Content:** Investigation into 3MF retrieval methods (FTPS, Cloud API) for multi-color filament tracking

**Status:** Historical research document; solution was abandoned in favor of fuel gauge system

**Recommendation:** Move to `docs/archive/` or delete

---

### Category B: Possibly Archive (Needs Confirmation)

#### 1. **Unreferenced Archive Directory**

| Path | Size | Status |
|------|------|--------|
| `bambu_3mf_usage/` | 17 files | Not referenced in any HA config |

**Contents:**
- 7 Python modules (`bambu_3mf_usage.py`, `ftps_client.py`, `map_filaments.py`, `parse_3mf.py`, `run_after_print.py`, `webhook_server.py`, `__init__.py`)
- `README.md` (4KB)
- `requirements.txt`
- `config.example.json`
- `testdata/` (5 files: 3MF samples, JSON fixtures)

**Analysis:**
- Standalone CLI tool for 3MF parsing
- NOT integrated with Home Assistant (removed in Phase 3.2)
- NOT deployed by `manage_ha.sh` (removed in Phase 2B.2)
- NOT referenced in automations, scripts, or docs

**Current References:**
- Only self-references within `bambu_3mf_usage/README.md`
- `result.json` appears in grep results (test fixture)

**Recommendation Options:**
1. **Keep as archive** - Rename to `archive/bambu_3mf_usage/` for historical reference
2. **Delete** - No longer needed; fuel gauge system replaced it
3. **Extract to separate repo** - If tool has standalone value

**User Decision Required:** Is this code worth keeping?

---

#### 2. **Root-Level Markdown Files (14 with 0 references)**

**Unreferenced Documentation (0 references):**
| File | Topic | Last Modified | Archive? |
|------|-------|---------------|----------|
| `BAMBU_LAN_MIGRATION.md` | Bambu printer LAN mode migration | Feb 10 | Possibly historical |
| `SPOOLMAN_CONNECTION_CHECK.md` | Spoolman connectivity diagnosis | Feb 11 | Possibly historical |
| `SPOOLMAN_FILAMENT_AUTOMATIONS.md` | Automation design notes | Feb 10 | Possibly historical |
| `SPOOLMAN_SLOT3_FIX.md` | Specific bug fix documentation | Feb 11 | Historical |
| `SPOOLMAN_SWAP_SPOOL.md` | Spool swapping procedure | Feb 10 | Possibly current |
| `SPOOL_LIST_TEMPLATE_CHECK.md` | Template sensor debugging | Feb 11 | Historical |
| `SPOOL_MANAGEMENT_SUMMARY.md` | Summary of spool management | Feb 10 | Overview doc |
| `SPOOL_MANAGEMENT_TEST_PLAN.md` | Test procedures | Feb 10 | Possibly current |
| `SPOOL_MANAGEMENT_UX_UPDATE.md` | UX improvement notes | Feb 11 | Historical |
| `TAPO_C111_CAMERA.md` | Camera setup notes | Feb 11 | Setup doc |
| `TAPO_C111_STEPS.md` | Camera configuration steps | Feb 10 | Setup doc |
| `SPOOLMAN_INTEGRATION_DIAGNOSIS.md` | Integration troubleshooting | Feb 11 | Diagnosis doc |
| `SPOOL_MANAGEMENT_GET_WORKING.md` | Getting started guide | Feb 10 | Setup doc |

**Analysis:**
- Most appear to be session notes, troubleshooting logs, or change documentation
- 0 references = not linked from README or other docs
- May still be valuable as historical records or runbooks

**Referenced Documentation (1-2 references):**
| File | References | Context |
|------|------------|---------|
| `README.md` | 1 | Main repo README (active) |
| `AMS_SLOT_MANAGER.md` | 2 | Referenced in code/docs |
| `SPOOL_MANAGEMENT_GET_WORKING.md` | 2 | Referenced in code/docs |
| `SPOOLMAN_WHEN_YOU_SWAP.md` | 1 | Operational procedure |
| `BAMBU_CLOUD_FALLBACK.md` | 1 | Operational procedure |

**Recommendation:**
1. **Keep:** `README.md`, `AMS_SLOT_MANAGER.md`, `SPOOL_MANAGEMENT_GET_WORKING.md`, `SPOOLMAN_WHEN_YOU_SWAP.md`
2. **Archive:** All others to `docs/archive/session_notes/` or `docs/archive/troubleshooting/`
3. **Delete:** Any that are truly obsolete after review

**User Decision Required:** Review each file and decide archive vs. delete

---

### Category C: In Active Use

#### 1. **Core Configuration Files** ✅

| File | Purpose | Status |
|------|---------|--------|
| `configuration.yaml` | Main HA config | ✅ Active |
| `automations.yaml` | Automations | ✅ Active |
| `scripts.yaml` | Scripts | ✅ Active |
| `secrets.yaml` | Secrets (gitignored) | ✅ Active |
| `go2rtc.yaml` | Camera streams | ✅ Active |
| `dashboard.yaml` | Symlink to stage | ✅ Active |

---

#### 2. **Dashboards** ✅

| File | Purpose | Status |
|------|---------|--------|
| `dashboards/dashboard.stage.yaml` | Stage dashboard | ✅ Active |
| `dashboards/dashboard.test.storage.yaml` | Test dashboard (storage mode) | ✅ Active |
| `dashboards/README_TEST_STORAGE.md` | Dashboard workflow notes | ✅ Active |

---

#### 3. **Deployment Tools** ✅

| File | Purpose | Status |
|------|---------|--------|
| `scripts/manage_ha.sh` | Deployment script | ✅ Active (cleaned) |
| `scripts/deploy.env` | Deploy config (gitignored) | ✅ Active |
| `scripts/deploy.env.example` | Deploy template | ✅ Active (cleaned) |

---

#### 4. **Spoolman Import Tools** ✅

| Directory | Files | Purpose | Status |
|-----------|-------|---------|--------|
| `spoolman_import/` | 7 Python scripts + docs | CSV export/import for Spoolman | ✅ Active |

**Files:**
- `export_spools.py`, `import_spools.py`, `update_spools.py`, `validate_spools.py`
- `merge_weighing_into_spools.py`, `set_empty_initial_weight.py`
- `SPOOLMAN_INVENTORY.md`, `README.md`
- `spools.csv`, `weighing_sheet.csv`

**Referenced by:** `scripts/manage_ha.sh` (`--spoolman-export`, `--spoolman-import`, `--spoolman-update`)

---

## Helpers Analysis (configuration.yaml)

### Defined Input Text Helpers

| Helper | Defined in Config | Referenced in automations.yaml | Referenced in scripts.yaml | Status |
|--------|-------------------|-------------------------------|---------------------------|--------|
| `ams_slot_1_spool_id` | ✅ | ✅ | ✅ | Active |
| `ams_slot_2_spool_id` | ✅ | ✅ | ✅ | Active |
| `ams_slot_3_spool_id` | ✅ | ✅ | ✅ | Active |
| `ams_slot_4_spool_id` | ✅ | ✅ | ✅ | Active |
| `ams_slot_5_spool_id` | ✅ | ✅ | ✅ | Active |
| `ams_slot_6_spool_id` | ✅ | ✅ | ✅ | Active |
| `spoolman_base_url` | ✅ | ❌ | ✅ | Active (scripts only) |
| `p1s_last_active_tray` | ✅ | ✅ | ❌ | Active |
| `p1s_last_tray_entity` | ✅ | ✅ | ❌ | Active |
| `p1s_trays_used_this_print` | ✅ | ✅ | ❌ | Active |

**Note:** `p1s_tray_remaining_start_json` and `p1s_tray_remaining_end_json` are **UI-created helpers** (storage mode), not in `configuration.yaml`.

### All Helpers Are Referenced ✅

No orphaned helpers found in `configuration.yaml`.

---

## Files Referencing Removed 3MF Pipeline

### 1. **bambu_3mf_usage/** (Archive Directory)

**All files in this directory reference the removed 3MF pipeline:**
- `bambu_3mf_usage.py` - Main CLI (6 references to "bambu_3mf")
- `run_after_print.py` - HA wrapper script
- `README.md` - Documentation (4 references)
- `webhook_server.py` - Alternative integration method
- `result.json`, `testdata/` - Test fixtures

**Status:** Unreferenced archive

---

### 2. **docs/3MF_RETRIEVAL_FEASIBILITY.md**

**Content:** Research document investigating 3MF retrieval methods (FTPS, Cloud API, printer cache)

**References:** Discusses AppDaemon approach and wrapper design (both removed)

**Status:** Historical research, solution abandoned in favor of fuel gauge

---

### 3. **scripts/deploy.env** (User's Private Config)

**Issue:** Line 17 contains:
```bash
REMOTE_APPDAEMON_APPS_PATH=/addon_configs/a0d7b954_appdaemon/apps
```

**Status:** User's gitignored config file; can be manually cleaned

**Action:** User should remove obsolete `REMOTE_APPDAEMON_APPS_PATH` from their private `deploy.env`

---

### 4. **@ File** (Sensitive Duplicate)

**Content:** Duplicate of `scripts/deploy.env` with sensitive credentials

**Issue:** Contains AppDaemon path reference + tokens

**Action:** Delete immediately

---

## Categorized Findings

### 🚨 **CRITICAL (Immediate Action Required)**

| Item | Risk | Action |
|------|------|--------|
| **`@` file in root** | Sensitive credentials exposed | **DELETE** |

---

### ⚠️ **HIGH PRIORITY (Should Archive/Remove)**

| Item | Reason | Recommendation |
|------|--------|----------------|
| `python_scripts/` directory | Empty, no longer used | **Remove directory** |
| `appdaemon/` directory | Empty subdirectory, no longer used | **Remove directory** |
| `docs/3MF_RETRIEVAL_FEASIBILITY.md` | References removed features | **Move to `docs/archive/`** |

---

### ℹ️ **MEDIUM PRIORITY (Consider Archiving)**

| Item | Reason | Recommendation |
|------|--------|----------------|
| `bambu_3mf_usage/` directory | Unreferenced archive (17 files) | **Move to `archive/bambu_3mf_usage/` OR delete** |
| 14 unreferenced root `.md` files | Session notes, no longer linked | **Move to `docs/archive/session_notes/`** |

**Unreferenced markdown files:**
- `BAMBU_LAN_MIGRATION.md`
- `SPOOLMAN_CONNECTION_CHECK.md`
- `SPOOLMAN_FILAMENT_AUTOMATIONS.md`
- `SPOOLMAN_SLOT3_FIX.md`
- `SPOOLMAN_SWAP_SPOOL.md`
- `SPOOL_LIST_TEMPLATE_CHECK.md`
- `SPOOL_MANAGEMENT_SUMMARY.md`
- `SPOOL_MANAGEMENT_TEST_PLAN.md`
- `SPOOL_MANAGEMENT_UX_UPDATE.md`
- `TAPO_C111_CAMERA.md`
- `TAPO_C111_STEPS.md`
- `SPOOLMAN_INTEGRATION_DIAGNOSIS.md`
- `SPOOL_MANAGEMENT_GET_WORKING.md` (2 references - possibly still useful)
- `SPOOLMAN_WHEN_YOU_SWAP.md` (1 reference - possibly still useful)

---

### ✅ **LOW PRIORITY (Keep, In Active Use)**

**All core HA configuration:**
- `configuration.yaml`, `automations.yaml`, `scripts.yaml`, `secrets.yaml`, `go2rtc.yaml`
- `dashboards/dashboard.stage.yaml`
- `scripts/manage_ha.sh`, `scripts/deploy.env.example`
- `spoolman_import/` (all files)
- `README.md`

**Referenced documentation:**
- `AMS_SLOT_MANAGER.md` (2 references)
- `BAMBU_CLOUD_FALLBACK.md` (1 reference)

---

## Helpers Not in configuration.yaml (UI-Created)

The following helpers are used by automations but **not defined in `configuration.yaml`** (created in HA UI):

| Helper | Used By | Status |
|--------|---------|--------|
| `input_text.p1s_tray_remaining_start_json` | Fuel gauge automations | ✅ Active (UI helper) |
| `input_text.p1s_tray_remaining_end_json` | Fuel gauge automations | ✅ Active (UI helper) |

**Note:** These are **intentionally** not in `configuration.yaml` because they were created via HA's UI. This is normal and expected.

---

## Recommended Actions

### Immediate (Critical)

```bash
# 1. Delete sensitive file
rm /Users/jdempsey/code/home_assistant/@

# 2. Verify not in git
git log --all --full-history -- "@"

# 3. If committed, consider git-filter-branch or BFG to remove from history
```

---

### High Priority (Cleanup)

```bash
# Remove empty directories
rmdir /Users/jdempsey/code/home_assistant/python_scripts
rm -rf /Users/jdempsey/code/home_assistant/appdaemon

# Archive obsolete 3MF research
mkdir -p docs/archive
mv docs/3MF_RETRIEVAL_FEASIBILITY.md docs/archive/
```

---

### Medium Priority (Organization)

```bash
# Option A: Archive bambu_3mf_usage
mkdir -p archive
mv bambu_3mf_usage archive/

# Option B: Delete bambu_3mf_usage
rm -rf bambu_3mf_usage

# Archive session notes
mkdir -p docs/archive/session_notes
mv BAMBU_LAN_MIGRATION.md docs/archive/session_notes/
mv SPOOLMAN_CONNECTION_CHECK.md docs/archive/session_notes/
mv SPOOLMAN_FILAMENT_AUTOMATIONS.md docs/archive/session_notes/
mv SPOOLMAN_SLOT3_FIX.md docs/archive/session_notes/
mv SPOOL_LIST_TEMPLATE_CHECK.md docs/archive/session_notes/
mv SPOOLMAN_INTEGRATION_DIAGNOSIS.md docs/archive/session_notes/
mv SPOOL_MANAGEMENT_SUMMARY.md docs/archive/session_notes/
mv SPOOL_MANAGEMENT_TEST_PLAN.md docs/archive/session_notes/
mv SPOOL_MANAGEMENT_UX_UPDATE.md docs/archive/session_notes/
mv TAPO_C111_CAMERA.md docs/archive/session_notes/
mv TAPO_C111_STEPS.md docs/archive/session_notes/

# Keep operational docs in root (or move to docs/)
# (AMS_SLOT_MANAGER.md, SPOOLMAN_SWAP_SPOOL.md, SPOOLMAN_WHEN_YOU_SWAP.md, SPOOL_MANAGEMENT_GET_WORKING.md)
```

---

### Low Priority (User Config)

**User should manually clean their private `scripts/deploy.env`:**
- Remove line: `REMOTE_APPDAEMON_APPS_PATH=/addon_configs/a0d7b954_appdaemon/apps`

*(This file is gitignored, so no commit needed)*

---

## Summary Statistics

| Category | Count | Total Size |
|----------|-------|------------|
| **Empty directories** | 2 | 0 bytes |
| **Sensitive files** | 1 | 728 bytes |
| **Unreferenced docs** | 15 | ~60 KB |
| **Archive directories** | 1 (`bambu_3mf_usage/`) | ~50 KB |
| **Active config files** | 6 | ~100 KB |
| **Active docs** | 3-5 | ~20 KB |

**Potential cleanup:** ~110 KB of obsolete files + 2 empty directories

---

## Next Steps

### Phase B (If User Approves):

1. **Delete `@` file** (CRITICAL)
2. **Remove empty directories** (`python_scripts/`, `appdaemon/`)
3. **Archive or delete `bambu_3mf_usage/`**
4. **Move unreferenced docs to `docs/archive/`**
5. **Update gitignore if needed**

### Phase C (Optional):

1. **Organize remaining root docs** into `docs/` subdirectories
2. **Create `docs/runbooks/`** for operational procedures
3. **Create `docs/setup/`** for setup guides

---

**Analysis complete.** No files were modified. Awaiting user approval for cleanup actions.
