"""Config flow for Tuneshine."""
from __future__ import annotations

import logging
import socket
from typing import Any

_LOGGER = logging.getLogger(__name__)

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import TuneshineApiClient, TuneshineApiError
from .const import CONF_DEVICE_NAME, DOMAIN


class TuneshineConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tuneshine."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._host: str | None = None
        self._hardware_id: str | None = None
        self._device_name: str | None = None

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle mDNS discovery."""
        # Use the IPv4 address directly — avoids IPv6 latency issues on some
        # networks where .local resolution defaults to a slower IPv6 path.
        host = str(discovery_info.ip_address)

        session = async_get_clientsession(self.hass)
        client = TuneshineApiClient(host, session)

        try:
            await client.async_health_check()
            state = await client.async_get_state()
        except TuneshineApiError:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(state.hardware_id)
        # If already configured, update the stored IP and abort.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._host = host
        self._hardware_id = state.hardware_id
        self._device_name = state.name or f"Tuneshine {state.hardware_id[-4:]}"

        self.context["title_placeholders"] = {
            "name": self._device_name,
            "host": host,
        }

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered device."""
        if user_input is not None:
            return self._async_create_entry()

        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": self._device_name,
                "host": self._host,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()

            # Resolve .local mDNS hostnames to an IPv4 address so we always
            # store a plain IP and avoid IPv6 slow-path issues.
            if host.endswith(".local") or host.endswith(".local."):
                try:
                    results = await self.hass.async_add_executor_job(
                        socket.getaddrinfo,
                        host.rstrip("."),
                        None,
                        socket.AF_INET,
                    )
                    host = results[0][4][0]
                except OSError:
                    errors["base"] = "cannot_connect"

            if not errors:
                session = async_get_clientsession(self.hass)
                client = TuneshineApiClient(host, session)
                try:
                    await client.async_health_check()
                    state = await client.async_get_state()
                except TuneshineApiError:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error connecting to Tuneshine at %s", host)
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(state.hardware_id)
                    self._abort_if_unique_id_configured()

                    self._host = host
                    self._hardware_id = state.hardware_id
                    self._device_name = (
                        state.name or f"Tuneshine {state.hardware_id[-4:]}"
                    )
                    return self._async_create_entry()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    def _async_create_entry(self) -> ConfigFlowResult:
        """Create the config entry."""
        return self.async_create_entry(
            title=self._device_name,
            data={
                CONF_HOST: self._host,
                CONF_DEVICE_NAME: self._device_name,
            },
        )
