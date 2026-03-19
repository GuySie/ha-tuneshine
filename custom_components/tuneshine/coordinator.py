"""DataUpdateCoordinator for TuneShine."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TuneshineApiClient, TuneshineConnectionError, TuneshineState
from .const import CONF_DEVICE_NAME, DOMAIN, POLL_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class TuneshineDataUpdateCoordinator(DataUpdateCoordinator[TuneshineState]):
    """Coordinator for a single TuneShine device."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: TuneshineApiClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL_SECONDS),
            # Skip listener callbacks when polled data is identical to previous.
            # TuneshineState uses @dataclass __eq__, so unchanged state won't
            # trigger unnecessary state machine writes at the 10s poll rate.
            always_update=False,
        )
        self.client = client
        self._entry = entry

    async def _async_update_data(self) -> TuneshineState:
        """Fetch state from device."""
        try:
            state = await self.client.async_get_state()
        except TuneshineConnectionError as err:
            raise UpdateFailed(
                f"Error communicating with TuneShine device: {err}"
            ) from err

        # Keep the config entry title in sync with the device name.
        current_name = state.name or state.hardware_id
        if self._entry.title != current_name:
            self.hass.config_entries.async_update_entry(
                self._entry,
                title=current_name,
                data={**self._entry.data, CONF_DEVICE_NAME: current_name},
            )

        return state
