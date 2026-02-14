#!/usr/bin/env bash

set -e

if [[ -z "$HOME_ASSISTANT_URL" || -z "$HOME_ASSISTANT_TOKEN" ]]; then
  echo "ERROR: HOME_ASSISTANT_URL or HOME_ASSISTANT_TOKEN not set."
  exit 1
fi

ha_get() {
  curl -s -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
    "$HOME_ASSISTANT_URL/api/states/$1" | jq -r '.state'
}

ha_post() {
  curl -s -X POST \
    -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$2" \
    "$HOME_ASSISTANT_URL/api/services/$1" >/dev/null
}

echo "🔄 Resetting helpers..."
ha_post "input_text/set_value" '{"entity_id":"input_text.p1s_init_seed_debug","value":"RESET"}'
ha_post "input_text/set_value" '{"entity_id":"input_text.p1s_tray_remaining_start_json","value":"{}"}'

echo
echo "📊 Current state (before print):"
echo "print_status     = $(ha_get sensor.p1s_01p00c5a3101668_print_status)"
echo "trays_used       = '$(ha_get input_text.p1s_trays_used_this_print)'"
echo "debug            = $(ha_get input_text.p1s_init_seed_debug)"
echo "start_json       = $(ha_get input_text.p1s_tray_remaining_start_json)"

echo
echo "🚀 Start a print now..."
read -p "Press ENTER once the printer status shows PRINTING/RUNNING..."

echo
echo "⏳ Monitoring for 30 seconds..."
for i in {1..15}; do
  sleep 2
  status=$(ha_get sensor.p1s_01p00c5a3101668_print_status)
  trays=$(ha_get input_text.p1s_trays_used_this_print)
  debug=$(ha_get input_text.p1s_init_seed_debug)
  start=$(ha_get input_text.p1s_tray_remaining_start_json)

  echo "-----"
  echo "print_status = $status"
  echo "trays_used   = '$trays'"
  echo "debug        = $debug"
  echo "start_json   = $start"
done

echo
echo "✅ Test complete."
