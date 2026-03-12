"""
FilamentIQ Base — shared config validation and entity prefix construction.

All FilamentIQ AppDaemon apps inherit from FilamentIQBase.
Entity naming: sensor.{prefix}_{sensor_name} where prefix = printer_model + printer_serial (lowercased).
ha-bambulab tray entities: sensor.{prefix}_ams_{ams_entity_idx}_tray_{tray_idx}
  - AMS Pro (4 trays): ams_entity_idx=1, tray_idx=1..4
  - AMS HT: ams_entity_idx=128 or 129, tray_idx=1
active_tray sensor uses ams_index 0 for first AMS, 128/129 for HT.
"""

import hassapi as hass


def _default_ams_units():
    """Default AMS layout: AMS Pro (slots 1-4) + HT (slots 5-6)."""
    return [
        {"type": "ams_2_pro", "ams_index": 0, "slots": [1, 2, 3, 4]},
        {"type": "ams_ht", "ams_index": 128, "slots": [5]},
        {"type": "ams_ht", "ams_index": 129, "slots": [6]},
    ]


def build_slot_mappings(prefix: str, ams_units=None):
    """Build TRAY_ENTITY_BY_SLOT, SLOT_BY_TRAY_ENTITY, AMS_TRAY_TO_SLOT, CANONICAL_LOCATION_BY_SLOT.

    ams_units: list of {type, ams_index, slots}. Default: AMS Pro + HT.
    Returns: (tray_entity_by_slot, slot_by_tray_entity, ams_tray_to_slot, canonical_location_by_slot)
    """
    if ams_units is None:
        ams_units = _default_ams_units()

    tray_entity_by_slot = {}
    ams_tray_to_slot = {}
    canonical_location_by_slot = {}

    for unit in ams_units:
        ams_index = int(unit.get("ams_index", 0))
        slots = unit.get("slots", [])
        unit_type = str(unit.get("type", "ams_2_pro"))

        # ha-bambulab: ams_1 for first unit (ams_index 0), ams_128/129 for HT
        ams_entity_idx = 1 if ams_index == 0 else ams_index

        for i, slot in enumerate(slots):
            slot = int(slot)
            tray_idx = i + 1  # 1-based in entity
            entity_id = f"sensor.{prefix}_ams_{ams_entity_idx}_tray_{tray_idx}"
            tray_entity_by_slot[slot] = entity_id
            ams_tray_to_slot[(ams_index, i)] = slot  # tray_index 0-based for active_tray
            # CANONICAL: AMS1_Slot1, AMS128_Slot1, AMS129_Slot1
            loc_ams = 1 if ams_index == 0 else ams_index
            canonical_location_by_slot[slot] = f"AMS{loc_ams}_Slot{tray_idx}"

    slot_by_tray_entity = {v: k for k, v in tray_entity_by_slot.items()}
    return tray_entity_by_slot, slot_by_tray_entity, ams_tray_to_slot, canonical_location_by_slot


class FilamentIQBase(hass.Hass):
    """Base class for FilamentIQ apps. Provides config validation and entity prefix building."""

    def _validate_config(self, required_keys: list, typed_keys: dict = None,
                         range_keys: dict = None) -> None:
        """Validate config: presence, type, and range.

        required_keys: list of key names that must be present and truthy.
        typed_keys: {key: (type_cls, default)} — validate type if key present.
            For bool: value must be actual bool (not string "yes" or int 1).
            For int/float: value is cast via type_cls(); ValueError on failure.
        range_keys: {key: (min_val, max_val)} — validate range after type check.
            None = no bound on that side.
        """
        errors = []

        # Phase 1: required keys (presence check)
        missing = [k for k in required_keys if not self.args.get(k)]
        if missing:
            for key in missing:
                msg = f"Required config key '{key}' is missing"
                self.log(f"CONFIG_ERROR {msg}", level="ERROR")
                errors.append(msg)

        # Phase 2: type validation
        if typed_keys:
            for key, (type_cls, default) in typed_keys.items():
                raw = self.args.get(key)
                if raw is None:
                    continue  # optional key absent, will use default
                if type_cls is bool:
                    if not isinstance(raw, bool):
                        msg = (f"Config key '{key}' must be bool, "
                               f"got {type(raw).__name__}: {raw!r}")
                        self.log(f"CONFIG_ERROR {msg}", level="ERROR")
                        errors.append(msg)
                else:
                    try:
                        type_cls(raw)
                    except (ValueError, TypeError):
                        msg = (f"Config key '{key}' must be {type_cls.__name__}, "
                               f"got {type(raw).__name__}: {raw!r}")
                        self.log(f"CONFIG_ERROR {msg}", level="ERROR")
                        errors.append(msg)

        # Phase 3: range validation
        if range_keys:
            for key, (min_val, max_val) in range_keys.items():
                raw = self.args.get(key)
                if raw is None:
                    continue
                try:
                    val = float(raw)
                except (ValueError, TypeError):
                    continue  # type error already caught above
                if min_val is not None and val < min_val:
                    msg = (f"Config key '{key}' must be >= {min_val}, "
                           f"got {val}")
                    self.log(f"CONFIG_ERROR {msg}", level="ERROR")
                    errors.append(msg)
                if max_val is not None and val > max_val:
                    msg = (f"Config key '{key}' must be <= {max_val}, "
                           f"got {val}")
                    self.log(f"CONFIG_ERROR {msg}", level="ERROR")
                    errors.append(msg)

        if errors:
            raise ValueError(
                f"FilamentIQ config errors: {'; '.join(errors)}"
            )

        self.log("CONFIG_VALID", level="INFO")

    def _check_spoolman_connectivity(self) -> None:
        """Check if Spoolman is reachable. WARNING on failure (non-blocking)."""
        url = str(self.args.get("spoolman_url", "")).rstrip("/")
        if not url:
            return
        import urllib.request
        try:
            req = urllib.request.Request(f"{url}/api/v1/info", method="GET")
            urllib.request.urlopen(req, timeout=5)
            self.log(f"SPOOLMAN_REACHABLE url={url}", level="INFO")
        except Exception as exc:
            self.log(
                f"SPOOLMAN_UNREACHABLE url={url} error={exc}",
                level="WARNING",
            )

    def _build_entity_prefix(self) -> str:
        """Construct entity prefix from printer_model + printer_serial (lowercased).

        Example: printer_model='p1s', printer_serial='01P00C5A3101668'
        → prefix = 'p1s_01p00c5a3101668'

        Entity names follow: sensor.{prefix}_{sensor_name}
        """
        model = str(self.args.get("printer_model", "p1s")).strip().lower()
        serial = str(self.args.get("printer_serial", "")).strip().lower()
        if not serial:
            return model
        return f"{model}_{serial}"

    def _build_slot_mappings(self):
        """Build slot mappings from self.args['ams_units']. Returns (tray_entity_by_slot, slot_by_tray_entity, ams_tray_to_slot, canonical_location_by_slot)."""
        prefix = self._build_entity_prefix()
        ams_units = self.args.get("ams_units")
        return build_slot_mappings(prefix, ams_units)

    def _get_all_slots(self) -> list:
        """Return sorted list of slot numbers from ams_units config."""
        tray_entity_by_slot, _, _, _ = self._build_slot_mappings()
        return sorted(tray_entity_by_slot.keys())
