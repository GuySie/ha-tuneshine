"""The TuneShine integration."""
from __future__ import annotations

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TuneshineApiClient
from .coordinator import TuneshineDataUpdateCoordinator
from .entity import TuneshineConfigEntry

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: TuneshineConfigEntry) -> bool:
    """Set up TuneShine from a config entry."""
    session = async_get_clientsession(hass)
    client = TuneshineApiClient(entry.data[CONF_HOST], session)
    coordinator = TuneshineDataUpdateCoordinator(hass, client, entry)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TuneshineConfigEntry) -> bool:
    """Unload a TuneShine config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
