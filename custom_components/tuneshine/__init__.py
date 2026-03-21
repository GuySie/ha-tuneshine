"""The Tuneshine integration."""
from __future__ import annotations

import functools
import logging
import socket

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TuneshineApiClient
from .const import CONF_SOURCE_ENTITY_ID, DOMAIN, INPUT_MODE_REMOTE, INPUT_MODE_SENDSPIN, INPUT_MODE_SOURCE
from .coordinator import TuneshineDataUpdateCoordinator
from .entity import TuneshineConfigEntry
from .sendspin import SendspinWebSocketView

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.NUMBER, Platform.SELECT, Platform.SENSOR]

# Guard so the WebSocket view is only registered once even when multiple
# Tuneshine devices are configured.
_SENDSPIN_VIEW_REGISTERED = "_sendspin_view_registered"


async def async_setup_entry(hass: HomeAssistant, entry: TuneshineConfigEntry) -> bool:
    """Set up Tuneshine from a config entry."""
    session = async_get_clientsession(hass)
    client = TuneshineApiClient(entry.data[CONF_HOST], session)
    coordinator = TuneshineDataUpdateCoordinator(hass, client, entry)

    await coordinator.async_config_entry_first_refresh()

    # Index coordinator by hardware_id so SendspinWebSocketView can look it up.
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][coordinator.data.hardware_id] = coordinator

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(coordinator.async_cleanup_source_listener)

    # Register the WebSocket view once across all entries.
    if not hass.data[DOMAIN].get(_SENDSPIN_VIEW_REGISTERED):
        hass.http.register_view(SendspinWebSocketView())
        hass.data[DOMAIN][_SENDSPIN_VIEW_REGISTERED] = True
        _LOGGER.debug("Registered Sendspin WebSocket view at /api/sendspin/{hardware_id}")

    # Wire mDNS callbacks so the coordinator can register/unregister at runtime.
    coordinator._async_register_mdns = functools.partial(
        _async_register_sendspin_mdns, hass, entry, coordinator
    )
    coordinator._async_unregister_mdns = functools.partial(
        _async_unregister_sendspin_mdns, hass, coordinator
    )

    # Activate the configured input mode.
    if coordinator.input_mode == INPUT_MODE_SENDSPIN:
        await _async_register_sendspin_mdns(hass, entry, coordinator)
    elif coordinator.input_mode == INPUT_MODE_SOURCE:
        await coordinator.async_setup_source_entity(entry.options.get(CONF_SOURCE_ENTITY_ID))
    # INPUT_MODE_REMOTE: nothing to activate — device shows cloud content passively.

    return True


async def _async_unregister_sendspin_mdns(
    hass: HomeAssistant,
    coordinator: TuneshineDataUpdateCoordinator,
) -> None:
    """Unregister the Sendspin mDNS service for this device."""
    mdns_info = getattr(coordinator, "_sendspin_mdns_info", None)
    if mdns_info is None:
        return
    try:
        from homeassistant.components.zeroconf import async_get_instance

        zc = await async_get_instance(hass)
        await zc.async_unregister_service(mdns_info)
        coordinator._sendspin_mdns_info = None
        _LOGGER.debug("Unregistered Sendspin mDNS service for %s", coordinator.data.hardware_id)
    except Exception:
        _LOGGER.debug("Failed to unregister Sendspin mDNS service (may already be gone)")


async def _async_register_sendspin_mdns(
    hass: HomeAssistant,
    entry: TuneshineConfigEntry,
    coordinator: TuneshineDataUpdateCoordinator,
) -> None:
    """Register a _sendspin._tcp.local. mDNS service for this device (idempotent)."""
    if getattr(coordinator, "_sendspin_mdns_info", None) is not None:
        return  # already registered
    try:
        from homeassistant.components.zeroconf import async_get_instance
        from zeroconf import ServiceInfo

        state = coordinator.data
        ha_port = hass.http.server_port
        device_name = state.name or "Tuneshine"
        hardware_id = state.hardware_id
        path = f"/api/sendspin/{hardware_id}"

        # Resolve the local IP that other devices on the LAN can reach HA on.
        _LOGGER.debug(
            "Sendspin mDNS setup: device=%r hardware_id=%s ha_port=%d path=%s",
            device_name,
            hardware_id,
            ha_port,
            path,
        )
        local_ip = _get_local_ip()
        if not local_ip:
            _LOGGER.warning(
                "Sendspin: could not determine local IP — mDNS advertisement skipped"
            )
            return
        _LOGGER.debug("Sendspin: resolved local IP as %s", local_ip)

        zc = await async_get_instance(hass)

        # Build a unique service name; the hardware_id ensures uniqueness per device.
        service_name = f"{device_name} ({hardware_id})._sendspin._tcp.local."

        info = ServiceInfo(
            type_="_sendspin._tcp.local.",
            name=service_name,
            addresses=[socket.inet_aton(local_ip)],
            port=ha_port,
            properties={"path": path},
        )

        await zc.async_register_service(info)
        _LOGGER.debug(
            "Registered Sendspin mDNS service %r at %s:%d%s",
            service_name,
            local_ip,
            ha_port,
            path,
        )

        # Store so we can unregister on unload.
        coordinator._sendspin_mdns_info = info

    except Exception:
        _LOGGER.exception(
            "Failed to register Sendspin mDNS advertisement — "
            "Music Assistant may not auto-discover this device"
        )


def _get_local_ip() -> str | None:
    """Return the local IP address HA is reachable on."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


async def async_unload_entry(hass: HomeAssistant, entry: TuneshineConfigEntry) -> bool:
    """Unload a Tuneshine config entry."""
    coordinator: TuneshineDataUpdateCoordinator = entry.runtime_data

    # Unregister the Sendspin mDNS advertisement (no-op if not currently registered).
    await _async_unregister_sendspin_mdns(hass, coordinator)

    # Remove hardware_id index entry.
    hass.data.get(DOMAIN, {}).pop(coordinator.data.hardware_id, None)

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
