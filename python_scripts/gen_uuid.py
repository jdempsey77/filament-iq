"""Generate a UUID4-ish string and write it to an input_text helper.

Called via: python_script.gen_uuid with data: {target: "input_text.xxx"}
Sandbox-safe: NO imports. Uses only hash() and id() for entropy.
"""
target = data.get("target") or data.get("entity_id") or "input_text.spoolman_new_spool_uuid"
logger.info("gen_uuid: start target=%s", target)

try:
    seed_time = hass.states.get("sensor.time")
    seed_time_str = seed_time.state if seed_time else ""
    seeds = [target, str(id(hass)), str(id(hass.states)), seed_time_str]
    h = ""
    for i in range(8):
        v = abs(hash("".join(seeds) + str(i)))
        h += format(v, '016x')[:8]
    h = h[:32]
    variant = "89ab"[abs(hash(h)) % 4]
    new_uuid = "{}-{}-4{}-{}{}-{}".format(
        h[0:8], h[8:12], h[13:16],
        variant, h[17:20], h[20:32]
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
