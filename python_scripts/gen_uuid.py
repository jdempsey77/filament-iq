"""Generate a UUID4 and write it to an input_text helper.

Called via: python_script.gen_uuid with data: {target: "input_text.xxx"}
"""
target = data.get("target", "input_text.spoolman_new_spool_uuid")
import uuid
new_uuid = str(uuid.uuid4())
hass.services.call("input_text", "set_value", {"entity_id": target, "value": new_uuid})
logger.info("gen_uuid: wrote %s to %s", new_uuid, target)
