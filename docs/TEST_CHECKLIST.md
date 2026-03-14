─────────────────────────────────────────────────────────────
TEST CHECKLIST — agent must confirm all that apply
─────────────────────────────────────────────────────────────
Before declaring TEST: PASS, confirm each applicable item:

HAPPY PATH
[ ] Primary success case tested with realistic values
[ ] Log output contains expected INFO message
[ ] Spoolman PATCH/USE called with correct arguments

GUARD / SKIP CONDITIONS
[ ] Each skip condition has its own test
[ ] Skipped case produces no Spoolman write
[ ] Skipped case logs expected DEBUG/WARNING message

CONFIG TOGGLES
[ ] Feature disabled via config key → entire feature skipped
[ ] Non-default config values tested where relevant

DRY RUN
[ ] dry_run=True → no Spoolman writes (PATCH, USE, or GET)
[ ] dry_run=True → correct log message emitted

ERROR / FAILURE PATHS
[ ] External call failure (Spoolman GET returns None) → no crash
[ ] External call failure → WARNING logged
[ ] Exception in method → caught, does not block caller

INPUT VALIDATION
[ ] None input → handled gracefully
[ ] Zero / negative numeric input → skip or guard fires
[ ] Out-of-range value → skip or guard fires

EXISTING TESTS
[ ] All previously passing tests still pass
[ ] No test file imports broken by changes

For each unchecked item: either confirm it is not applicable
to this feature, or add a test to cover it.
Report checklist results in TEST REPORT.
