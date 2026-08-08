"""Tuneshine sensor entities."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
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
    """Set up Tuneshine sensor entities from a config entry."""
    coordinator: TuneshineDataUpdateCoordinator = entry.runtime_data
    async_add_entities([
        TuneshineDisplayModeSensor(coordinator),
        TuneshineWifiStatusSensor(coordinator),
    ])


class TuneshineDisplayModeSensor(TuneshineEntity, SensorEntity):
    """Sensor reporting how the Tuneshine display is currently being driven."""

    _attr_translation_key = "display_mode"

    def __init__(self, coordinator: TuneshineDataUpdateCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.hardware_id}_display_mode"

    @property
    def native_value(self) -> DisplayMode:
        """Return the current display mode."""
        return self.coordinator.display_mode


class TuneshineWifiStatusSensor(TuneshineEntity, SensorEntity):
    """Diagnostic sensor reporting the device's wifi connection status."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "wifi_status"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: TuneshineDataUpdateCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.hardware_id}_wifi_status"

    @property
    def native_value(self) -> str:
        """Return the current wifi connection status."""
        return self.coordinator.data.wifi.status

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Return the wifi SSID and connection mode as extra attributes."""
        return {
            "ssid": self.coordinator.data.wifi.ssid,
            "mode": self.coordinator.data.mode,
        }
