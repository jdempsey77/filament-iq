# Incident: 8-Hour Print - Wrong Spool Decremented

## What Happened

1. **No HA notification** - Finish automation stopped early (start snapshot empty)
2. **Mutex still ON** - Finish automation didn't clear it (now fixed)
3. **Wrong spool decremented** - Bambu Blue Hawaii (Shelf) went 1000→603g (~397g)
4. **Correct filament** - Overture blue (actual print) was NOT decremented

## Root Cause: Start Snapshot Empty

**Why:** The print started BEFORE Step 1 was deployed. The init automation (`p1s_remaining_snapshot_init`) only triggers when `print_status` goes TO `running`/`printing`. Since the print was already running when you deployed, init never ran.

**Result:** `input_text.p1s_tray_remaining_start_json` stayed `{}`. When print finished, the finish automation saw empty start_dict and stopped early (no decrement, no notification).

## Root Cause: Wrong Spool Decremented

**Our HA automation did NOT decrement** - we stopped before the decrement loop.

**The decrement came from elsewhere.** Likely candidates:

### 1. Bambu Lab HA Integration + Spoolman Link (MOST LIKELY)

The Bambu Lab integration can link to Spoolman. When configured, it may:
- Read the active filament from the 3MF/print
- Map "Blue" (or similar) to a Spoolman spool
- Call `use_spool_filament` when print finishes

**If the mapping is wrong** (e.g. "Blue" → Bambu Blue Hawaii instead of Overture blue), it would decrement the wrong spool.

**Check:**
- Settings → Devices & Services → Bambu Lab
- Click Configure (or the integration)
- Look for Spoolman / filament mapping settings
- See which spool ID is linked to which tray/filament

### 2. Bambu Studio / Slicer Plugin

If you use Bambu Studio with a Spoolman plugin, it might decrement on print finish based on its own mapping.

### 3. Another Automation

Search automations for `use_spool_filament` or `spoolman` - ensure only our finish automation calls it for print-complete.

## Fixes Applied

### 1. Mutex Always Clears (automations.yaml)

The finish automation now **clears `p1s_print_active` FIRST** (before any conditions). So even when we stop early, the mutex will clear.

### 2. Notification on Empty Start (automations.yaml)

When start_dict is empty, we now **notify** the user instead of failing silently. You'll see:
- "P1S Filament Tracking: Start Snapshot Empty"
- Explains why no decrement happened
- Suggests manual reconciliation

### 3. Added Missing Variables (automations.yaml)

`is_failed` and `should_decrement` were referenced but not defined. Added to variables block.

## Immediate Actions for You

### 1. Manually Clear Mutex (Right Now)

Developer Tools → States → `input_boolean.p1s_print_active` → Turn OFF

(Or the fix will clear it on next print finish; but clear it now so state is correct.)

### 2. Restore Bambu Blue Hawaii Spool

The wrong spool was decremented by ~397g. To fix:
- Spoolman UI or HA: Find spool "Bambu Lab - Blue Hawaii"
- Add back ~397g to remaining_weight (or set to correct value)
- Or use Spoolman API: PATCH spool with corrected remaining_weight

### 3. Decrement Overture Blue Manually

Find the Overture blue spool in Spoolman. Estimate grams used (~397g from the print) and:
- Call `spoolman.use_spool_filament` with that spool ID and use_weight
- Or update remaining_weight directly via Spoolman UI

### 4. Disable Conflicting Decrement Source

**Find what decremented the wrong spool:**
- Check Bambu integration Spoolman settings
- Disable or correct the mapping
- Ensure only our HA finish automation decrements on print complete

### 5. Verify Slot Mapping

Before next print, ensure:
- `input_text.ams_slot_N_spool_id` for the slot with Overture blue = Overture blue spool ID
- NOT the Bambu Blue Hawaii spool ID

## Prevention for Next Print

1. **Start a FRESH print** after deploying fixes (init will run)
2. **Verify** `p1s_print_active` turns ON automatically when print starts
3. **Verify** slot mappings match physical trays
4. **Check** Bambu/Spoolman integration - disable auto-decrement if it conflicts

## Deploy the Fixes

```bash
./scripts/workflow.sh --automations "fix: Always clear mutex on finish; notify on empty start snapshot" --no-push
# Or with restart:
./scripts/manage_ha.sh --automations --restart
```

---

*Generated: Post 8-hour print incident*
