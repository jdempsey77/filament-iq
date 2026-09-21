"""
FilamentIQ Base — shared config validation and entity prefix construction.

All FilamentIQ AppDaemon apps inherit from FilamentIQBase.
Entity naming: sensor.{prefix}_{sensor_name} where prefix = printer_model + printer_serial (lowercased).
ha-bambulab tray entities: sensor.{prefix}_ams_{ams_entity_idx}_tray_{tray_idx}
  - AMS Pro (4 trays): ams_entity_idx=1, tray_idx=1..4
  - AMS HT: ams_entity_idx=128, 129, or 130, tray_idx=1
active_tray sensor uses ams_index 0 for first AMS, 128/129/130 for HT.
"""

import json
import os

import hassapi as hass

TERMINAL_PRINT_STATES = frozenset({
    "finish", "finished", "completed",
    "failed", "error",
    "cancelled", "canceled",
})


def _default_ams_units():
    """Default AMS layout: AMS Pro (slots 1-4) + HT (slots 5-7)."""
    return [
        {"type": "ams_2_pro", "ams_index": 0, "slots": [1, 2, 3, 4]},
        {"type": "ams_ht", "ams_index": 128, "slots": [5]},
        {"type": "ams_ht", "ams_index": 129, "slots": [6]},
        {"type": "ams_ht", "ams_index": 130, "slots": [7]},
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

        if unit_type == "external":
            # External spool: dedicated ha-bambulab entity, not an AMS tray.
            # active_ams_index=255, active_tray_index=0 per ha-bambulab source.
            for slot in slots:
                slot = int(slot)
                entity_id = f"sensor.{prefix}_externalspool_external_spool"
                tray_entity_by_slot[slot] = entity_id
                ams_tray_to_slot[(255, 0)] = slot
                canonical_location_by_slot[slot] = "External"
            continue  # skip AMS tray loop

        # ha-bambulab: ams_1 for first unit (ams_index 0), ams_128/129 for HT
        ams_entity_idx = 1 if ams_index == 0 else ams_index

        for i, slot in enumerate(slots):
            slot = int(slot)
            tray_idx = i + 1  # 1-based in entity
            entity_id = f"sensor.{prefix}_ams_{ams_entity_idx}_tray_{tray_idx}"
            tray_entity_by_slot[slot] = entity_id
            ams_tray_to_slot[(ams_index, i)] = slot  # tray_index 0-based for active_tray
            # CANONICAL: AMS1_Slot1, AMS128_Slot1, AMS129_Slot1, AMS130_Slot1
            loc_ams = 1 if ams_index == 0 else ams_index
            canonical_location_by_slot[slot] = f"AMS{loc_ams}_Slot{tray_idx}"

    slot_by_tray_entity = {v: k for k, v in tray_entity_by_slot.items()}
    return tray_entity_by_slot, slot_by_tray_entity, ams_tray_to_slot, canonical_location_by_slot


class FilamentIQBase(hass.Hass):
    """Base class for FilamentIQ apps. Provides config validation and entity prefix building."""

    FIQ_ENABLED_ENTITY = "input_boolean.filament_iq_enabled"
    FIQ_DEFAULT_DATA_DIR = "/addon_configs/a0d7b954_appdaemon/data/filament_iq"
    FIQ_REGISTRY_MAX_ENTRIES = 200

    def __init_subclass__(cls, **kwargs):
        """Wrap each concrete app's initialize() so the notification registry
        loads, self-heals (dismiss-all if already paused), and the pause
        listener registers automatically — no per-app wiring required.

        Two hard guarantees, verified by tests:
        - The subclass's own initialize() always runs, no matter what
          _fiq_bootstrap() does (even an exception escaping every internal
          try/except there is still caught here).
        - A sentinel on the wrapper (_fiq_is_wrapped) makes wrapping
          idempotent, so a partial module reload that re-triggers
          __init_subclass__ on an already-wrapped method cannot stack a
          second bootstrap call onto every initialize().
        """
        super().__init_subclass__(**kwargs)
        orig_initialize = cls.__dict__.get("initialize")
        if orig_initialize is not None and not getattr(orig_initialize, "_fiq_is_wrapped", False):
            def _fiq_wrapped_initialize(self):
                # AppDaemon introspects initialize()'s signature before
                # calling it and rejects anything other than (self) — no
                # *args/**kwargs passthrough here, and it is always called
                # with zero arguments in practice.
                try:
                    self._fiq_bootstrap()
                except Exception as exc:
                    try:
                        self.log(f"FIQ_BOOTSTRAP_UNEXPECTED_FAILURE error={exc}", level="WARNING")
                    except Exception:
                        pass
                return orig_initialize(self)
            _fiq_wrapped_initialize._fiq_is_wrapped = True
            cls.initialize = _fiq_wrapped_initialize

    def _fiq_bootstrap(self) -> None:
        """Run once, right before an app's own initialize() logic. Every
        failure here is caught and logged — a registry/listener problem must
        never block an app from starting (fail open, always)."""
        try:
            self._fiq_load_registry()
        except Exception as exc:
            self.log(f"FIQ_BOOTSTRAP_REGISTRY_LOAD_FAILED error={exc}", level="WARNING")
            self._fiq_registry = []
        try:
            if not self.fiq_enabled():
                self.fiq_dismiss_all()
        except Exception as exc:
            self.log(f"FIQ_BOOTSTRAP_DISMISS_FAILED error={exc}", level="WARNING")
        try:
            entity = str(self.args.get("enabled_entity", self.FIQ_ENABLED_ENTITY))
            self.listen_state(self._fiq_on_pause_state_change, entity)
        except Exception as exc:
            self.log(f"FIQ_BOOTSTRAP_LISTENER_FAILED error={exc}", level="WARNING")

    def _fiq_on_pause_state_change(self, entity, attribute, old, new, kwargs) -> None:
        """Fires on every state change of the switch entity. Only acts on a
        transition TO off — resuming (-> on) never replays or re-creates
        anything. Idempotent: off->off, a restart while already off, and a
        double-fire are all harmless because fiq_dismiss_all() just re-clears
        an already-empty (or already-consistent) registry.
        """
        if new != "off":
            return
        try:
            self.fiq_dismiss_all()
        except Exception as exc:
            self.log(f"FIQ_PAUSE_DISMISS_FAILED error={exc}", level="WARNING")

    def _fiq_registry_path(self) -> str:
        data_dir = str(self.args.get("data_dir", "") or "").strip().rstrip("/")
        if not data_dir:
            data_dir = self.FIQ_DEFAULT_DATA_DIR
        name = str(getattr(self, "name", "") or "app")
        return os.path.join(data_dir, f"active_notifications_{name}.json")

    def _fiq_ensure_registry_loaded(self) -> None:
        if getattr(self, "_fiq_registry", None) is None:
            self._fiq_load_registry()

    def _fiq_load_registry(self) -> None:
        """Load this app's on-disk notification registry. Never raises: a
        missing file is an empty registry, a corrupt one logs a warning and
        starts empty."""
        self._fiq_registry = []
        path = self._fiq_registry_path()
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as exc:
            self.log(f"FIQ_REGISTRY_CORRUPT path={path} error={exc}", level="WARNING")
            return
        if isinstance(data, list):
            self._fiq_registry = [str(x) for x in data]
        else:
            self.log(f"FIQ_REGISTRY_CORRUPT path={path} error=not_a_list", level="WARNING")

    def _fiq_save_registry(self) -> None:
        path = self._fiq_registry_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w") as f:
                json.dump(self._fiq_registry, f)
            os.replace(tmp_path, path)
        except Exception as exc:
            self.log(f"FIQ_REGISTRY_SAVE_FAILED path={path} error={exc}", level="WARNING")

    def _fiq_registry_add(self, notification_id) -> None:
        self._fiq_ensure_registry_loaded()
        nid = str(notification_id)
        if nid in self._fiq_registry:
            self._fiq_registry.remove(nid)
        self._fiq_registry.append(nid)
        if len(self._fiq_registry) > self.FIQ_REGISTRY_MAX_ENTRIES:
            self._fiq_registry = self._fiq_registry[-self.FIQ_REGISTRY_MAX_ENTRIES:]
        self._fiq_save_registry()

    def fiq_dismiss(self, notification_id) -> None:
        """Dismiss a persistent notification and remove it from the registry.
        Ungated — dismissal must work while paused."""
        nid = str(notification_id)
        try:
            self.call_service("persistent_notification/dismiss", notification_id=nid)
        except Exception as exc:
            self.log(f"FIQ_DISMISS_FAILED notification_id={nid} error={exc}", level="WARNING")
        self._fiq_ensure_registry_loaded()
        if nid in self._fiq_registry:
            self._fiq_registry.remove(nid)
            self._fiq_save_registry()

    def fiq_dismiss_all(self) -> None:
        """Dismiss every notification this app instance has registered, then
        truncate the registry. The registry is authoritative — never iterate
        HA's persistent-notification list and delete by prefix/wildcard,
        that would destroy unrelated HA notices."""
        self._fiq_ensure_registry_loaded()
        for nid in list(self._fiq_registry):
            try:
                self.call_service("persistent_notification/dismiss", notification_id=nid)
            except Exception as exc:
                self.log(f"FIQ_DISMISS_ALL_ITEM_FAILED notification_id={nid} error={exc}", level="WARNING")
        self._fiq_registry = []
        self._fiq_save_registry()

    def fiq_enabled(self) -> bool:
        """Read the master pause switch. Fail open: missing/unknown/unavailable/errors -> True.

        Warns once per app instance (not per call) so a typo'd entity name
        never silently floods the log while still never silently pausing
        the system.
        """
        entity = str(self.args.get("enabled_entity", self.FIQ_ENABLED_ENTITY))
        try:
            state = self.get_state(entity)
        except Exception as exc:
            self._fiq_enabled_warn_once(
                f"FIQ_ENABLED_READ_FAILED entity={entity} error={exc}"
            )
            return True
        if state in (None, "", "unknown", "unavailable"):
            self._fiq_enabled_warn_once(
                f"FIQ_ENABLED_UNRESOLVED entity={entity} state={state!r}"
            )
            return True
        return str(state) == "on"

    def _fiq_enabled_warn_once(self, msg: str) -> None:
        if getattr(self, "_fiq_enabled_warned", False):
            return
        self._fiq_enabled_warned = True
        self.log(msg, level="WARNING")

    def fiq_notify(self, title, message, notification_id=None, push=False,
                    push_service=None) -> None:
        """Single notification chokepoint: gates persistent + push notifications
        on the master pause switch. When paused, logs one debug line and sends
        nothing.
        """
        if not self.fiq_enabled():
            self.log(f"FIQ_PAUSED suppressed notification: {title}", level="DEBUG")
            return
        if notification_id is not None:
            self.call_service(
                "persistent_notification/create",
                title=title,
                message=message,
                notification_id=notification_id,
            )
            self._fiq_registry_add(notification_id)
        if push:
            service = push_service or getattr(self, "notify_service", None)
            if service:
                self.call_service(f"notify/{service}", title=title, message=message)

    @property
    def dry_run(self) -> bool:
        """True when explicitly configured dry_run, OR the master switch is paused."""
        return bool(getattr(self, "_dry_run_cfg", False)) or not self.fiq_enabled()

    @dry_run.setter
    def dry_run(self, value) -> None:
        self._dry_run_cfg = bool(value)

    def fiq_write_blocked(self, what: str, target: str, detail=None) -> bool:
        """Spoolman write chokepoint gate. Reuses dry_run (config OR paused
        switch) rather than adding a second mechanism. Call at the top of every
        method that issues a non-GET Spoolman request: if this returns True,
        log WOULD_<what> and return None without touching the network.
        """
        if not self.dry_run:
            return False
        self.log(
            f"WOULD_{what} target={target} detail={detail!r} "
            f"(dry_run/paused — Spoolman write suppressed)",
            level="INFO",
        )
        return True

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

        Example: printer_model='p1s', printer_serial='YOUR_PRINTER_SERIAL'
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
