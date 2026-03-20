"""TuneShine sensor entities."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DisplayMode
from .coordinator import TuneshineDataUpdateCoordinator
from .entity import TuneshineConfigEntry, TuneshineEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuneshineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TuneShine sensor entities from a config entry."""
    coordinator: TuneshineDataUpdateCoordinator = entry.runtime_data
    async_add_entities([TuneshineDisplayModeSensor(coordinator)])


class TuneshineDisplayModeSensor(TuneshineEntity, SensorEntity):
    """Sensor reporting how the TuneShine display is currently being driven."""

    _attr_translation_key = "display_mode"

    def __init__(self, coordinator: TuneshineDataUpdateCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.hardware_id}_display_mode"

    @property
    def native_value(self) -> DisplayMode:
        """Return the current display mode."""
        return self.coordinator.display_mode
