"""Base entity for Tuneshine."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DOMAIN, MANUFACTURER
from .coordinator import TuneshineDataUpdateCoordinator

# Typed config entry alias — entry.runtime_data is TuneshineDataUpdateCoordinator.
type TuneshineConfigEntry = ConfigEntry[TuneshineDataUpdateCoordinator]


class TuneshineEntity(CoordinatorEntity[TuneshineDataUpdateCoordinator]):
    """Base class for all Tuneshine entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TuneshineDataUpdateCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.data.hardware_id)},
            name=coordinator.data.name or entry.data[CONF_DEVICE_NAME],
            manufacturer=MANUFACTURER,
            model="Tuneshine LED Display",
            sw_version=coordinator.data.firmware_version,
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )
