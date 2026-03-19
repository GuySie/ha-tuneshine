"""Select entity for TuneShine source media player."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SOURCE_ENTITY_ID
from .coordinator import TuneshineDataUpdateCoordinator
from .entity import TuneshineConfigEntry, TuneshineEntity

_NONE_OPTION = "none"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuneshineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the source media player select entity."""
    async_add_entities([TuneshineSourceSelectEntity(entry.runtime_data, entry)])


class TuneshineSourceSelectEntity(TuneshineEntity, SelectEntity):
    """Select entity to choose a source media player to mirror on TuneShine."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:television-play"
    _attr_translation_key = "source_entity"

    def __init__(
        self,
        coordinator: TuneshineDataUpdateCoordinator,
        entry: TuneshineConfigEntry,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.data.hardware_id}_source_entity"
        # Initialise from persisted options so the correct value is shown on startup.
        self._attr_current_option = entry.options.get(CONF_SOURCE_ENTITY_ID, _NONE_OPTION)

    @property
    def options(self) -> list[str]:
        """Return available options: none + all registered media_player entity IDs."""
        registry = er.async_get(self.hass)
        return [_NONE_OPTION] + sorted(
            entry.entity_id
            for entry in registry.entities.values()
            if entry.domain == "media_player" and not entry.disabled_by
        )

    async def async_select_option(self, option: str) -> None:
        """Update the source subscription in-place and persist the selection."""
        entity_id = None if option == _NONE_OPTION else option

        # Update internal state immediately so async_write_ha_state reflects it.
        self._attr_current_option = option

        # Update subscription without reloading the entry.
        await self.coordinator.async_setup_source_entity(entity_id)

        # Persist to options for restart survival.
        new_options = dict(self._entry.options)
        if entity_id:
            new_options[CONF_SOURCE_ENTITY_ID] = entity_id
        else:
            new_options.pop(CONF_SOURCE_ENTITY_ID, None)
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.async_write_ha_state()
