"""Generate a UUID4-ish string and write it to an input_text helper.

Called via: python_script.gen_uuid with data: {target: "input_text.xxx"}
Sandbox-safe: NO imports, NO time/datetime/random/uuid references.
Entropy from hash() + id() of stable runtime objects only.
"""
target = data.get("target") or data.get("entity_id") or "input_text.spoolman_new_spool_uuid"
logger.info("gen_uuid: start target=%s", target)

try:
    s0 = str(id(hass))
    s1 = str(id(hass.states))
    s2 = str(id(data))
    s3 = target
    h = ""
    for i in range(8):
        v = abs(hash(s0 + s1 + s2 + s3 + str(i)))
        h = h + format(v, '016x')[:8]
    h = h[:32]
    variant = "89ab"[abs(hash(h)) % 4]
    new_uuid = h[0:8] + "-" + h[8:12] + "-4" + h[13:16] + "-" + variant + h[17:20] + "-" + h[20:32]
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
