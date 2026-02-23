# Reusable jq: normalize Spoolman extra.rfid_tag_uid (single source of truth).
# Accepts array or {"items": [...]} via spools(). Defines normalize_rfid_tag_uid for use via include.
#
# normalize_rfid_tag_uid: fromjson if possible (must not error), tostring, strip whitespace, remove quotes,
#   uppercase, convert "0000...", "UNKNOWN", "NULL", "null", empty to "".
#
# Usage: jq -L scripts/lib -e 'include "spoolman_normalize"; spools() | ...'

def spools:
  if type == "array" then .
  elif (type == "object" and (.items | type == "array")) then .items
  else []
  end;

def normalize_rfid_tag_uid:
  if . == null or . == "" then ""
  else
    (if type == "string" then (try (fromjson) catch .) else . end | tostring)
    | gsub("^[\\s\"]+|[\\s\"]+$"; "")
    | gsub("\\\\"; "")
    | ascii_upcase
    | if . == "" or . == "0000000000000000" or . == "UNKNOWN" or . == "UNAVAILABLE" or . == "NONE" or . == "NULL" or . == "null" then "" else . end
  end;
