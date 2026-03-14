#!/usr/bin/env bash
# ─── Filament IQ Dashboard Configurator ─────────────────────────────
# Replaces PRINTER_SERIAL placeholder with your Bambu Lab printer serial.
#
# Usage:
#   ./dashboards/scripts/configure_filament_iq.sh [-n] <printer_serial>
#   ./dashboards/scripts/configure_filament_iq.sh --reset
#
# Options:
#   -n, --dry-run   Show what would be changed without modifying files
#   --reset         Restore PRINTER_SERIAL placeholders (auto-detects serial)
#
# Examples:
#   ./dashboards/scripts/configure_filament_iq.sh p1s_01p00c5a3101668
#   ./dashboards/scripts/configure_filament_iq.sh -n p1s_01p00c5a3101668
#   ./dashboards/scripts/configure_filament_iq.sh --reset
#
# The script edits dashboards/filament_iq_dashboard.yaml in place.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(dirname "$SCRIPT_DIR")"
DASHBOARD_FILE="$DASHBOARD_DIR/filament_iq_dashboard.yaml"

DRY_RUN=false
RESET=false

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--dry-run)
      DRY_RUN=true
      shift
      ;;
    --reset)
      RESET=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [-n|--dry-run] <printer_serial>"
      echo "       $0 --reset"
      echo ""
      echo "Options:"
      echo "  -n, --dry-run   Show what would be changed without modifying files"
      echo "  --reset         Restore PRINTER_SERIAL placeholders (auto-detects serial)"
      echo ""
      echo "Arguments:"
      echo "  printer_serial  Your Bambu Lab entity prefix (e.g. p1s_01p00c5a3101668)"
      echo ""
      echo "Find yours in HA → Settings → Devices → your printer → entity IDs."
      exit 0
      ;;
    -*)
      echo "ERROR: Unknown option: $1"
      echo "Usage: $0 [-n|--dry-run] <printer_serial>"
      echo "       $0 --reset"
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

if [[ ! -f "$DASHBOARD_FILE" ]]; then
  echo "ERROR: Dashboard file not found at $DASHBOARD_FILE"
  echo "Run this script from the repo root."
  exit 1
fi

# ── Reset mode ──────────────────────────────────────────────────────
if [[ "$RESET" == true ]]; then
  # Already in placeholder state?
  PLACEHOLDER_COUNT=$(grep -c 'PRINTER_SERIAL' "$DASHBOARD_FILE" 2>/dev/null || true)
  if [[ "$PLACEHOLDER_COUNT" -gt 0 ]]; then
    echo "Dashboard already contains PRINTER_SERIAL placeholders ($PLACEHOLDER_COUNT found)."
    echo "Nothing to reset."
    exit 0
  fi

  # Auto-detect the configured serial from a known entity pattern
  # Look for sensor.<serial>_print_status which is unique to the printer
  # Use sed (macOS-compatible) instead of grep -P
  DETECTED=$(grep -o 'sensor\.[a-z0-9_]*_print_status' "$DASHBOARD_FILE" | head -1 | sed 's/^sensor\.//;s/_print_status$//' || true)

  if [[ -z "$DETECTED" ]]; then
    # Fallback: try light.<serial>_chamber_light
    DETECTED=$(grep -o 'light\.[a-z0-9_]*_chamber_light' "$DASHBOARD_FILE" | head -1 | sed 's/^light\.//;s/_chamber_light$//' || true)
  fi

  if [[ -z "$DETECTED" ]]; then
    # Fallback: try fan.<serial>_chamber_fan
    DETECTED=$(grep -o 'fan\.[a-z0-9_]*_chamber_fan' "$DASHBOARD_FILE" | head -1 | sed 's/^fan\.//;s/_chamber_fan$//' || true)
  fi

  if [[ -z "$DETECTED" ]]; then
    echo "ERROR: Could not detect printer serial in dashboard."
    echo "Dashboard may already be in placeholder state or has no printer entities."
    exit 1
  fi

  COUNT=$(grep -c "${DETECTED}" "$DASHBOARD_FILE" 2>/dev/null || true)

  echo "Detected serial: $DETECTED"
  echo "  Occurrences:   $COUNT"

  sed -i '' "s/${DETECTED}/PRINTER_SERIAL/g" "$DASHBOARD_FILE"

  echo ""
  echo "RESET COMPLETE — dashboard restored to placeholder state."
  echo "  File: $DASHBOARD_FILE"
  echo "  Replacements: $COUNT"
  exit 0
fi

# ── Configure mode ──────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 [-n|--dry-run] <printer_serial>"
  echo "       $0 --reset"
  echo ""
  echo "  printer_serial  Your Bambu Lab entity prefix (e.g. p1s_01p00c5a3101668)"
  echo ""
  echo "Find yours in HA → Settings → Devices → your printer → entity IDs."
  exit 1
fi

SERIAL="$1"

# Idempotency check — refuse to run if already configured
COUNT=$(grep -c 'PRINTER_SERIAL' "$DASHBOARD_FILE" 2>/dev/null || true)

if [[ "$COUNT" -eq 0 ]]; then
  echo "No PRINTER_SERIAL placeholders found — dashboard is already configured."
  echo "To reconfigure, run:  $0 --reset"
  exit 0
fi

if [[ "$DRY_RUN" == true ]]; then
  echo "DRY RUN — showing substitutions that would be made:"
  echo ""
  echo "  Pattern: PRINTER_SERIAL → $SERIAL"
  echo "  File:    $DASHBOARD_FILE"
  echo "  Count:   $COUNT occurrences"
  echo ""
  echo "Sample matches:"
  grep -n 'PRINTER_SERIAL' "$DASHBOARD_FILE" | head -10
  if [[ "$COUNT" -gt 10 ]]; then
    echo "  ... and $((COUNT - 10)) more"
  fi
  echo ""
  echo "DRY RUN — no files modified."
  exit 0
fi

# Replace all occurrences
sed -i '' "s/PRINTER_SERIAL/${SERIAL}/g" "$DASHBOARD_FILE"

echo "Configured dashboard for printer: $SERIAL"
echo "  File: $DASHBOARD_FILE"
echo "  Replacements: $COUNT"
echo ""
echo "Next steps:"
echo "  1. Copy the YAML into a new HA dashboard (Settings → Dashboards → Add → YAML)"
echo "  2. Or import via the HA Raw Configuration Editor"
echo "  3. Ensure these HACS frontends are installed:"
echo "     - mushroom (lovelace-mushroom)"
echo "     - card-mod"
echo "     - mod-card"
echo "     - button-card"
echo "     - flex-table-card (for Spool Inventory / Filament Library)"
echo "     - layout-card"
echo "     - browser_mod (for Add Spool / Add Filament popups)"
