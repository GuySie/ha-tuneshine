"""TuneShine brightness number entities."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import TuneshineApiClient, TuneshineState
from .coordinator import TuneshineDataUpdateCoordinator
from .entity import TuneshineConfigEntry, TuneshineEntity


@dataclass(frozen=True, kw_only=True)
class TuneshineNumberEntityDescription(NumberEntityDescription):
    """Extends NumberEntityDescription with typed accessor callables."""

    value_fn: Callable[[TuneshineState], int]
    set_value_fn: Callable[[TuneshineApiClient, int], Coroutine[Any, Any, None]]


NUMBERS: tuple[TuneshineNumberEntityDescription, ...] = (
    TuneshineNumberEntityDescription(
        key="brightness_active",
        translation_key="brightness_active",
        entity_category=EntityCategory.CONFIG,
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        # Disabled by default — secondary control; user opts in.
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.brightness.active,
        set_value_fn=lambda c, v: c.async_set_brightness(active=v),
    ),
    TuneshineNumberEntityDescription(
        key="brightness_idle",
        translation_key="brightness_idle",
        entity_category=EntityCategory.CONFIG,
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.brightness.idle,
        set_value_fn=lambda c, v: c.async_set_brightness(idle=v),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuneshineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TuneShine number entities from a config entry."""
    coordinator: TuneshineDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        TuneshineNumberEntity(coordinator, description) for description in NUMBERS
    )


class TuneshineNumberEntity(TuneshineEntity, NumberEntity):
    """A number entity for a TuneShine brightness setting."""

    entity_description: TuneshineNumberEntityDescription

    def __init__(
        self,
        coordinator: TuneshineDataUpdateCoordinator,
        description: TuneshineNumberEntityDescription,
    ) -> None:
        """Initialise the number entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.data.hardware_id}_{description.key}"
        )

    @property
    def native_value(self) -> int:
        """Return the current brightness value."""
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        """Set the brightness value on the device."""
        await self.entity_description.set_value_fn(
            self.coordinator.client, int(value)
        )
        await self.coordinator.async_request_refresh()
