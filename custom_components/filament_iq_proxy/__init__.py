"""Filament IQ Proxy — bridges Lovelace card to Spoolman REST API.

Registers a single HA service (filament_iq_proxy.api_call) that proxies
HTTP requests to Spoolman and fires response events back to the browser.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

DOMAIN = "filament_iq_proxy"
_LOGGER = logging.getLogger(__name__)

CONF_SPOOLMAN_URL = "spoolman_url"
DEFAULT_SPOOLMAN_URL = "http://192.168.4.124:7912"

VALID_METHODS = {"GET", "POST", "PATCH", "DELETE"}

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(
                    CONF_SPOOLMAN_URL, default=DEFAULT_SPOOLMAN_URL
                ): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

SERVICE_API_CALL = "api_call"

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("request_id"): cv.string,
        vol.Required("method"): vol.In(VALID_METHODS),
        vol.Required("path"): cv.string,
        vol.Optional("body"): dict,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Filament IQ Proxy component."""
    conf = config.get(DOMAIN, {})
    spoolman_url = conf.get(CONF_SPOOLMAN_URL, DEFAULT_SPOOLMAN_URL).rstrip("/")

    async def handle_api_call(call: ServiceCall) -> None:
        """Proxy request to Spoolman and fire response event."""
        request_id = call.data["request_id"]
        method = call.data["method"].upper()
        path = call.data["path"]
        body = call.data.get("body")

        url = f"{spoolman_url}{path}"

        try:
            async with aiohttp.ClientSession() as session:
                kwargs: dict[str, Any] = {
                    "timeout": aiohttp.ClientTimeout(total=10),
                }
                if body is not None and method in ("POST", "PATCH"):
                    kwargs["json"] = body

                async with session.request(method, url, **kwargs) as resp:
                    status = resp.status
                    try:
                        response_body = await resp.json(content_type=None)
                    except Exception:
                        response_body = None

            _LOGGER.info(
                "[filament_iq_proxy] %s %s -> %s", method, path, status
            )

        except aiohttp.ClientError as err:
            status = 503
            response_body = {"error": str(err)}
            _LOGGER.error("[filament_iq_proxy] Request failed: %s", err)
        except asyncio.TimeoutError:
            status = 408
            response_body = {"error": "Request timed out"}
            _LOGGER.error(
                "[filament_iq_proxy] Request timed out: %s %s", method, url
            )

        hass.bus.async_fire(
            "filament_iq_proxy_response",
            {
                "request_id": request_id,
                "status": status,
                "body": response_body,
            },
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_API_CALL,
        handle_api_call,
        schema=SERVICE_SCHEMA,
    )

    _LOGGER.info(
        "[filament_iq_proxy] Service %s.%s registered", DOMAIN, SERVICE_API_CALL
    )
    return True
