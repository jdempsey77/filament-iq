"""Generate a UUID4 and write it to an input_text helper.

Called via: python_script.gen_uuid with data: {target: "input_text.xxx"}
Sandbox-compatible (no uuid import). Uses time-based hashing.
"""
target = data.get("target") or data.get("entity_id") or "input_text.spoolman_new_spool_uuid"
logger.info("gen_uuid: start target=%s", target)

try:
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
    logger.info("gen_uuid: generated uuid=%s", new_uuid)
    hass.services.call(
        "input_text",
        "set_value",
        {"entity_id": target, "value": new_uuid},
        False
    )
    logger.info("gen_uuid: set_value ok target=%s", target)
except Exception as e:
    logger.error("gen_uuid: failed target=%s error=%s", target, e)
    raise
