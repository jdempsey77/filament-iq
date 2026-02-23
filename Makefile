SHELL := /bin/bash

SLOT ?= 4
TIMEOUT ?= 60

RUNNER := ./scripts/run_spool_system_testplan.py
PREFLIGHT_INPUT := ./scripts/preflight_input_text.sh
PREFLIGHT_DROPDOWN := ./scripts/preflight_spoolman_filament_dropdown.sh
PREFLIGHT_AMS := ./scripts/preflight_ams_matching.sh
PREFLIGHT_LOCATION := ./scripts/preflight_spoolman_location_update.sh
PREFLIGHT_SPOOLMAN_LOCATIONS := ./scripts/preflight_spoolman_locations.sh
PREFLIGHT_NO_LEGACY_LOCATIONS := ./scripts/preflight_no_legacy_locations.sh
PREFLIGHT_SPOOLMAN_EXTRA_JSON := ./scripts/preflight_spoolman_extra_json.sh

.PHONY: gates gate-phase0 gate-drift gate-phase2a gate-predicate snapshot-phase0 diff-phase0 promote-baseline check-phase0-baseline explain-phase0-digest test-all test-rfid test-nonrfid test-mismatch test-baseline deploy-scripts preflight-spoolman-extra-json \
  gates_phase0_baseline gates_phase0_hard test_phase0_freeze phase1_snapshot gates_phase1_match gates_phase1_identity gates_phase2_bind gates_phase3_tracking gates_phase4_ux gates_phase5_monitoring gates_phased

gates:
	@set -euo pipefail; \
	echo "== Running preflight gates =="; \
	$(PREFLIGHT_NO_LEGACY_LOCATIONS); \
	echo "PASS: preflight_no_legacy_locations"; \
	$(PREFLIGHT_INPUT); \
	echo "PASS: preflight_input_text"; \
	$(PREFLIGHT_DROPDOWN); \
	echo "PASS: preflight_spoolman_filament_dropdown"; \
	$(PREFLIGHT_AMS); \
	echo "PASS: preflight_ams_matching"; \
	$(PREFLIGHT_LOCATION); \
	echo "PASS: preflight_spoolman_location_update"; \
	$(PREFLIGHT_SPOOLMAN_LOCATIONS); \
	echo "PASS: preflight_spoolman_locations"; \
	if [ -n "$${SPOOLMAN_URL:-}" ]; then $(PREFLIGHT_SPOOLMAN_EXTRA_JSON); echo "PASS: preflight_spoolman_extra_json"; fi; \
	echo "ALL_GATES=PASS"

preflight-spoolman-extra-json:
	@$(PREFLIGHT_SPOOLMAN_EXTRA_JSON)

# Phase 0 RFID regression: feature flag OFF, preflights, snapshot, compare to baseline
GATE_PHASE0 := ./scripts/gate_phase0_rfid_regression.sh
SNAPSHOT_PHASE0 := ./scripts/snapshot_rfid_baseline.sh
PROMOTE_PHASE0 := ./scripts/promote_phase0_baseline.sh
ARTIFACTS := ./artifacts

gate-phase0:
	@$(GATE_PHASE0)

# Drift-prevention gates (deterministic; no live HA): RFID override block, location exclusivity, ignore placeholder HT
gate-drift:
	@set -e; \
	echo "== gate-drift (RFID override block, location exclusivity, ignore placeholder HT) =="; \
	./scripts/gates/gate_rfid_override_block.sh; \
	./scripts/gates/gate_location_exclusivity.sh; \
	./scripts/gates/gate_ignore_placeholder_ht.sh; \
	echo "ALL gate-drift PASS"

# Phase 2A: non-RFID usage predicate consistency with fixture
gate-phase2a:
	@./scripts/gates/gate_phase2a_nonrfid_usage_safe.sh

# Predicate consistency: no fake UUID; has_real_rfid (tag + tray_uuid) used in scripts/automations
gate-predicate:
	@./scripts/gates/gate_predicate_consistency.sh

# ----- Phased gate suite (RFID/non-RFID spool system) -----
# Idempotent, safe to run repeatedly. See docs/PHASED_GATES.md.
GATES_DIR := ./scripts/gates

# Phase 0 baseline: snapshot AMS trays + Spoolman RFID; requires HA and Spoolman env. Prints snapshot dir on success.
gates_phase0_baseline:
	@set -euo pipefail; \
	if [ -z "$${HOME_ASSISTANT_URL:-}" ]; then echo "gates_phase0_baseline FAIL: HOME_ASSISTANT_URL is required."; exit 1; fi; \
	if [ -z "$${HOME_ASSISTANT_TOKEN:-}" ]; then echo "gates_phase0_baseline FAIL: HOME_ASSISTANT_TOKEN is required."; exit 1; fi; \
	if [ -z "$${SPOOLMAN_URL:-}" ]; then echo "gates_phase0_baseline FAIL: SPOOLMAN_URL is required."; exit 1; fi; \
	REPO_ROOT="$$(pwd)" ./scripts/phase0_baseline_snapshot.sh

# Phase 0 HARD gate: snapshot + fail if duplicates or RFID in "New". Exit 0=clean, 10=duplicates, 11=New+RFID, 12=snapshot failure. Always prints snapshot path, dup_count, new_with_rfid_count.
gates_phase0_hard:
	@set -euo pipefail; \
	if [ -z "$${HOME_ASSISTANT_URL:-}" ]; then printf 'FAIL: PHASE 0 BASELINE DIRTY\n'; printf '  HOME_ASSISTANT_URL required\n'; exit 12; fi; \
	if [ -z "$${HOME_ASSISTANT_TOKEN:-}" ]; then printf 'FAIL: PHASE 0 BASELINE DIRTY\n'; printf '  HOME_ASSISTANT_TOKEN required\n'; exit 12; fi; \
	if [ -z "$${SPOOLMAN_URL:-}" ]; then printf 'FAIL: PHASE 0 BASELINE DIRTY\n'; printf '  SPOOLMAN_URL required\n'; exit 12; fi; \
	phase0_out=$$(mktemp); \
	REPO_ROOT="$$(pwd)" ./scripts/phase0_baseline_snapshot.sh > "$$phase0_out" 2>&1; snap_ret=$$?; \
	SNAPDIR=$$(grep '^SNAPSHOT_DIR=' "$$phase0_out" | sed 's/^SNAPSHOT_DIR=//' | tail -n1); \
	rm -f "$$phase0_out"; \
	if [ "$$snap_ret" -ne 0 ]; then \
	  printf 'snapshot path=%s\n' "$$SNAPDIR"; \
	  printf 'dup_count=0\n'; \
	  printf 'new_with_rfid_count=0\n'; \
	  printf 'FAIL: PHASE 0 BASELINE DIRTY\n'; \
	  printf '  snapshot failed (exit %s)\n' "$$snap_ret"; \
	  exit 12; fi; \
	DUP_COUNT=$$(jq 'length' "$$SNAPDIR/duplicate_rfid_report.json"); \
	NEW_COUNT=$$(jq 'length' "$$SNAPDIR/new_location_with_rfid.json"); \
	printf 'snapshot path=%s\n' "$$SNAPDIR"; \
	printf 'dup_count=%s\n' "$$DUP_COUNT"; \
	printf 'new_with_rfid_count=%s\n' "$$NEW_COUNT"; \
	if [ "$$DUP_COUNT" -gt 0 ]; then printf 'FAIL: PHASE 0 BASELINE DIRTY\n'; printf '  duplicate RFID tags: %s\n' "$$DUP_COUNT"; exit 10; fi; \
	if [ "$$NEW_COUNT" -gt 0 ]; then printf 'FAIL: PHASE 0 BASELINE DIRTY\n'; printf '  RFID spools in location New: %s\n' "$$NEW_COUNT"; exit 11; fi; \
	printf 'PASS: PHASE 0 BASELINE CLEAN\n'

# Phase 1 match resolution (read-only). Prints SNAPSHOT_DIR=...
phase1_snapshot:
	@set -euo pipefail; \
	if [ -z "$${HOME_ASSISTANT_URL:-}" ] || [ -z "$${HOME_ASSISTANT_TOKEN:-}" ] || [ -z "$${SPOOLMAN_URL:-}" ]; then printf 'PHASE1_FAIL: HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN, SPOOLMAN_URL required\n'; exit 22; fi; \
	REPO_ROOT="$$(pwd)" ./scripts/phase1_match_resolution.sh

# Phase 1 match gate: run phase1_snapshot, then fail if AMBIGUOUS_DUPLICATES or SPOOL_IN_NEW. Exit 0=clean, 20=ambiguous, 21=in New, 22=snapshot failure.
gates_phase1_match:
	@set -euo pipefail; \
	if [ -z "$${HOME_ASSISTANT_URL:-}" ]; then printf 'snapshot path=\n'; printf 'ambiguous_count=0\n'; printf 'in_new_count=0\n'; printf 'FAIL: PHASE 1 MATCH DIRTY\n'; printf '  HOME_ASSISTANT_URL required\n'; exit 22; fi; \
	if [ -z "$${HOME_ASSISTANT_TOKEN:-}" ]; then printf 'snapshot path=\n'; printf 'ambiguous_count=0\n'; printf 'in_new_count=0\n'; printf 'FAIL: PHASE 1 MATCH DIRTY\n'; printf '  HOME_ASSISTANT_TOKEN required\n'; exit 22; fi; \
	if [ -z "$${SPOOLMAN_URL:-}" ]; then printf 'snapshot path=\n'; printf 'ambiguous_count=0\n'; printf 'in_new_count=0\n'; printf 'FAIL: PHASE 1 MATCH DIRTY\n'; printf '  SPOOLMAN_URL required\n'; exit 22; fi; \
	phase1_out=$$(mktemp); \
	REPO_ROOT="$$(pwd)" ./scripts/phase1_match_resolution.sh > "$$phase1_out" 2>&1; snap_ret=$$?; \
	SNAPDIR=$$(grep '^SNAPSHOT_DIR=' "$$phase1_out" | sed 's/^SNAPSHOT_DIR=//' | tail -n1); \
	rm -f "$$phase1_out"; \
	if [ "$$snap_ret" -ne 0 ]; then \
	  printf 'snapshot path=%s\n' "$$SNAPDIR"; \
	  printf 'ambiguous_count=0\n'; \
	  printf 'in_new_count=0\n'; \
	  printf 'FAIL: PHASE 1 MATCH DIRTY\n'; \
	  printf '  phase1 snapshot failed (exit %s)\n' "$$snap_ret"; \
	  exit 22; fi; \
	AMBIGUOUS=$$(jq '[.[] | select(.resolution_status == "AMBIGUOUS_DUPLICATES")] | length' "$$SNAPDIR/match_results.json"); \
	IN_NEW=$$(jq '[.[] | select(.resolution_status == "SPOOL_IN_NEW")] | length' "$$SNAPDIR/match_results.json"); \
	printf 'snapshot path=%s\n' "$$SNAPDIR"; \
	printf 'ambiguous_count=%s\n' "$$AMBIGUOUS"; \
	printf 'in_new_count=%s\n' "$$IN_NEW"; \
	if [ "$$AMBIGUOUS" -gt 0 ]; then printf 'FAIL: PHASE 1 MATCH DIRTY\n'; printf '  ambiguous duplicates: %s\n' "$$AMBIGUOUS"; exit 20; fi; \
	if [ "$$IN_NEW" -gt 0 ]; then printf 'FAIL: PHASE 1 MATCH DIRTY\n'; printf '  spool in New: %s\n' "$$IN_NEW"; exit 21; fi; \
	printf 'PASS: PHASE 1 MATCH CLEAN\n'

# Self-test: writer freeze. No HA/Spoolman env required. Invoke script with minimal dummy args so gate runs before SPOOLMAN_URL/curl; assert exit 9 and refusal string in stdout/stderr.
test_phase0_freeze:
	@set -euo pipefail; \
	unset AMS_ALLOW_RFID_WRITES; \
	out=$$(mktemp); \
	code=0; ./scripts/spoolman_update_location_from_slot.sh 1 "a1b2c3d4e5f6789012345678abcdef01" "A71B987C00000100" "" "" 1 0 "" > "$$out" 2>&1 || code=$$?; \
	refusal="SPOOLMAN_WRITE_REFUSED: AMS_ALLOW_RFID_WRITES must be 1 to write RFID identity."; \
	if [ "$$code" -ne 9 ]; then echo "FAIL: test_phase0_freeze expected exit 9, got $$code"; cat "$$out"; rm -f "$$out"; exit 1; fi; \
	if ! grep -Fq "$$refusal" "$$out"; then echo "FAIL: test_phase0_freeze expected exact refusal message"; cat "$$out"; rm -f "$$out"; exit 1; fi; \
	rm -f "$$out"; \
	echo "PASS: test_phase0_freeze (writer freeze returns 9)"

gates_phase1_identity:
	@$(GATES_DIR)/gate_phase1_identity.sh

gates_phase2_bind:
	@$(GATES_DIR)/gate_phase2_bind.sh

gates_phase3_tracking:
	@$(GATES_DIR)/gate_phase3_tracking.sh

gates_phase4_ux:
	@$(GATES_DIR)/gate_phase4_ux.sh

gates_phase5_monitoring:
	@$(GATES_DIR)/gate_phase5_monitoring.sh

gates_phased:
	@set -e; \
	echo "== Phased gates (0..5) =="; \
	$(MAKE) gates_phase0_baseline; \
	$(MAKE) gates_phase1_identity; \
	$(MAKE) gates_phase2_bind; \
	$(MAKE) gates_phase3_tracking; \
	$(MAKE) gates_phase4_ux; \
	$(MAKE) gates_phase5_monitoring; \
	echo "GATES_PHASED=PASS"

snapshot-phase0:
	@ARTIFACTS_DIR="$(ARTIFACTS)" $(SNAPSHOT_PHASE0)

# Diff: if phase0_baseline exists, diff it vs latest run; else diff two most recent runs under phase0_runs
diff-phase0:
	@set -e; \
	RUNS="$(ARTIFACTS)/phase0_runs"; \
	BASE="$(ARTIFACTS)/phase0_baseline/digest.txt"; \
	LATEST=$$(find "$$RUNS" -maxdepth 1 -type d -name 'rfid_baseline_*' -print 2>/dev/null | sort | tail -n1); \
	if [ -f "$$BASE" ] && [ -n "$$LATEST" ] && [ -f "$$LATEST/digest.txt" ]; then \
	  echo "Diff: phase0_baseline vs latest run"; \
	  diff -u "$$BASE" "$$LATEST/digest.txt" || true; \
	elif [ -n "$$LATEST" ]; then \
	  PREV=$$(find "$$RUNS" -maxdepth 1 -type d -name 'rfid_baseline_*' -print 2>/dev/null | sort | tail -n2 | head -n1); \
	  if [ -n "$$PREV" ] && [ -f "$$PREV/digest.txt" ]; then \
	    echo "Diff: two most recent runs (no promoted baseline)"; \
	    diff -u "$$PREV/digest.txt" "$$LATEST/digest.txt" || true; \
	  else \
	    echo "Only one snapshot in $$RUNS. Run make snapshot-phase0 again or promote-baseline."; exit 1; \
	  fi; \
	else \
	  echo "No snapshots in $$RUNS. Run: make snapshot-phase0"; exit 1; \
	fi

# Optional: PROMOTE_NOTE="...", SNAPSHOT_DIR=/path, DRY_RUN=1
promote-baseline:
	@ARTIFACTS_DIR="$(ARTIFACTS)" PROMOTE_NOTE="$(PROMOTE_NOTE)" $(PROMOTE_PHASE0) $(SNAPSHOT_DIR)

# Print canonical identity-only JSON used for digest (debug "what changed"). Optional: SNAPSHOT_DIR=path or path to snapshot.json
explain-phase0-digest:
	@ARTIFACTS_DIR="$(ARTIFACTS)" ./scripts/explain_phase0_digest.sh $(SNAPSHOT_DIR)

# Validate promoted baseline exists and required files present (operator/CI confidence check)
check-phase0-baseline:
	@set -e; \
	BASE="$(ARTIFACTS)/phase0_baseline"; \
	if [ ! -d "$$BASE" ]; then echo "CHECK FAIL: phase0_baseline missing ($$BASE)"; exit 1; fi; \
	if [ ! -f "$$BASE/digest.txt" ]; then echo "CHECK FAIL: $$BASE/digest.txt missing"; exit 1; fi; \
	if [ ! -f "$$BASE/snapshot.json" ]; then echo "CHECK FAIL: $$BASE/snapshot.json missing"; exit 1; fi; \
	if [ ! -f "$$BASE/baseline_meta.json" ]; then echo "CHECK WARN: $$BASE/baseline_meta.json missing (optional)"; fi; \
	echo "CHECK OK: phase0_baseline present with digest.txt, snapshot.json"

test-all:
	@set -euo pipefail; \
	LOG_PATH="./artifacts/testplan_$$(date -u +%Y%m%d_%H%M%S)_all.log"; \
	echo "== Running spool system testplan (all) =="; \
	$(RUNNER) --slot "$(SLOT)" --scenario all --timeout "$(TIMEOUT)" --log "$$LOG_PATH"; \
	echo "TESTPLAN=PASS"; \
	echo "TESTPLAN_LOG=$$LOG_PATH"

test-rfid:
	@set -euo pipefail; \
	LOG_PATH="./artifacts/testplan_$$(date -u +%Y%m%d_%H%M%S)_rfid.log"; \
	echo "== Running spool system testplan (rfid) =="; \
	$(RUNNER) --slot "$(SLOT)" --scenario rfid --timeout "$(TIMEOUT)" --log "$$LOG_PATH"; \
	echo "TESTPLAN=PASS"; \
	echo "TESTPLAN_LOG=$$LOG_PATH"

test-nonrfid:
	@set -euo pipefail; \
	LOG_PATH="./artifacts/testplan_$$(date -u +%Y%m%d_%H%M%S)_nonrfid.log"; \
	echo "== Running spool system testplan (nonrfid) =="; \
	$(RUNNER) --slot "$(SLOT)" --scenario nonrfid --timeout "$(TIMEOUT)" --log "$$LOG_PATH"; \
	echo "TESTPLAN=PASS"; \
	echo "TESTPLAN_LOG=$$LOG_PATH"

test-mismatch:
	@set -euo pipefail; \
	LOG_PATH="./artifacts/testplan_$$(date -u +%Y%m%d_%H%M%S)_mismatch.log"; \
	echo "== Running spool system testplan (mismatch) =="; \
	$(RUNNER) --slot "$(SLOT)" --scenario mismatch --timeout "$(TIMEOUT)" --log "$$LOG_PATH"; \
	echo "TESTPLAN=PASS"; \
	echo "TESTPLAN_LOG=$$LOG_PATH"

test-baseline:
	@set -euo pipefail; \
	LOG_PATH="./artifacts/testplan_$$(date -u +%Y%m%d_%H%M%S)_baseline.log"; \
	echo "== Running spool system testplan (baseline-only, no prompts) =="; \
	$(RUNNER) --slot "$(SLOT)" --scenario baseline --timeout "$(TIMEOUT)" --no-prompt --log "$$LOG_PATH"; \
	echo "TESTPLAN=PASS"; \
	echo "TESTPLAN_LOG=$$LOG_PATH"

deploy-scripts:
	@set -euo pipefail; \
	./scripts/manage_ha.sh --scripts; \
	echo "DEPLOY_SCRIPTS=PASS"
