"""Tuneshine switch entities."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TuneshineDataUpdateCoordinator
from .entity import TuneshineConfigEntry, TuneshineEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuneshineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuneshine switch entities from a config entry."""
    coordinator: TuneshineDataUpdateCoordinator = entry.runtime_data
    async_add_entities([TuneshinePreserveArtworkSwitch(coordinator)])


class TuneshinePreserveArtworkSwitch(TuneshineEntity, SwitchEntity):
    """Switch to keep the last track's artwork (dimmed) instead of the idle image when playback stops."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "preserve_artwork"

    def __init__(self, coordinator: TuneshineDataUpdateCoordinator) -> None:
        """Initialise the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.hardware_id}_preserve_artwork"

    @property
    def is_on(self) -> bool:
        """Return whether preserve-artwork is enabled on the device."""
        return self.coordinator.data.preserve_artwork

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable preserve-artwork."""
        await self.coordinator.client.async_set_preserve_artwork(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable preserve-artwork."""
        await self.coordinator.client.async_set_preserve_artwork(False)
        await self.coordinator.async_request_refresh()
