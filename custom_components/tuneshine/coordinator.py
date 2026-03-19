"""DataUpdateCoordinator for TuneShine."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TuneshineApiClient, TuneshineApiError, TuneshineConnectionError, TuneshineState
from .const import CONF_DEVICE_NAME, DOMAIN, POLL_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

_NON_PLAYING_STATES = frozenset({
    STATE_UNAVAILABLE, STATE_UNKNOWN, "off", "idle", "paused", "standby"
})


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
        self._source_unsub: Callable[[], None] | None = None

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

    async def async_setup_source_entity(self, entity_id: str | None) -> None:
        """Set up or replace the source media player subscription in-place."""
        # Tear down any existing subscription first.
        if self._source_unsub is not None:
            self._source_unsub()
            self._source_unsub = None

        if not entity_id:
            return

        _LOGGER.debug("Setting up source listener for %s", entity_id)

        # Fire immediately to catch "already playing" at startup or on switch.
        state = self.hass.states.get(entity_id)
        if state is not None:
            await self._async_handle_source_state(state)

        @callback
        def _on_state_change(event: Event[EventStateChangedData]) -> None:
            new_state = event.data["new_state"]
            _LOGGER.debug(
                "Source state changed: %s -> %s",
                entity_id,
                new_state.state if new_state else "removed",
            )
            self.hass.async_create_task(self._async_handle_source_state(new_state))

        self._source_unsub = async_track_state_change_event(
            self.hass, [entity_id], _on_state_change
        )

    @property
    def has_source(self) -> bool:
        """Return True when actively following a source media player."""
        return self._source_unsub is not None

    @callback
    def async_cleanup_source_listener(self) -> None:
        """Unsubscribe from the source media player state listener."""
        if self._source_unsub is not None:
            self._source_unsub()
            self._source_unsub = None

    async def _async_handle_source_state(self, state: State | None) -> None:
        """Send or clear image based on the source media player's state."""
        try:
            if state is None or state.state in _NON_PLAYING_STATES:
                _LOGGER.debug("Source player not playing (%s), clearing image", state.state if state else "none")
                await self.client.async_clear_image()
            elif state.state == "playing":
                image_url = self._get_image_url(state)
                if image_url is None:
                    _LOGGER.debug("Source player has no image, clearing TuneShine")
                    await self.client.async_clear_image()
                else:
                    _LOGGER.debug("Sending image to TuneShine: %s", image_url)
                    await self.client.async_send_image(
                        image_url=image_url,
                        track_name=state.attributes.get("media_title"),
                        artist_name=state.attributes.get("media_artist"),
                        album_name=state.attributes.get("media_album_name"),
                        service_name=state.attributes.get("app_name") or "Home Assistant",
                    )
        except TuneshineApiError as err:
            _LOGGER.warning("Failed to update TuneShine from source player: %s", err)
            return
        await self.async_request_refresh()

    def _get_image_url(self, state: State) -> str | None:
        """Return an http:// image URL for the media player's current artwork.

        TuneShine only accepts http:// URLs. Relative entity_picture paths
        (the HA proxy) and plain http:// URLs are used directly. https:// URLs
        (e.g. Spotify CDN, where media_image_remotely_accessible=True) are
        routed through HA's public media player proxy endpoint so TuneShine
        receives an http:// address.
        """
        # Prefer entity_picture_local — some integrations (e.g. Music Assistant)
        # store the HA proxy path here alongside a remote https:// entity_picture.
        entity_picture = (
            state.attributes.get("entity_picture_local")
            or state.attributes.get("entity_picture")
        )
        if not entity_picture:
            return None

        try:
            base = get_url(self.hass, allow_internal=True, allow_ip=True)
        except NoURLAvailableError:
            base = ""

        # Relative path — already the HA proxy endpoint.
        if entity_picture.startswith("/"):
            return f"{base}{entity_picture}"

        # Plain http:// — acceptable as-is.
        if entity_picture.startswith("http://"):
            return entity_picture

        # https:// — TuneShine rejects https://.
        # Route through HA's media player proxy using the access_token.
        if entity_picture.startswith("https://"):
            access_token = state.attributes.get("access_token")
            if not access_token:
                return None
            return f"{base}/api/media_player_proxy/{state.entity_id}?token={access_token}"

        return None
