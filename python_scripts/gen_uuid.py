"""Generate a UUID4 and write it to an input_text helper.

Called via: python_script.gen_uuid with data: {target_entity: "input_text.xxx"}

NOTE: HA python_scripts cannot 'import uuid' (restricted sandbox).
UUID4 is built from time-based hashing as a fallback.
Prefer the Jinja template approach in scripts.yaml instead.
"""
target = data.get("target_entity", "input_text.spoolman_new_spool_uuid")

t = str(time.time()) + str(time.monotonic())
h = ""
for i in range(4):
    v = abs(hash(t + str(i)))
    h += format(v, '016x')[:16]
h = h[:32]
new_uuid = "{}-{}-4{}-{}{}-{}".format(
    h[0:8], h[8:12], h[13:16],
    "89ab"[abs(hash(h)) % 4], h[17:20], h[20:32]
)

hass.services.call("input_text", "set_value", {"entity_id": target, "value": new_uuid})
logger.info("gen_uuid: set %s to %s", target, new_uuid)
