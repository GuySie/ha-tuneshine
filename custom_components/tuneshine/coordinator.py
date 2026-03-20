"""DataUpdateCoordinator for TuneShine."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
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
        self._debounce_unsub: Callable[[], None] | None = None
        self._handler_task: asyncio.Task | None = None

    async def _async_update_data(self) -> TuneshineState:
        """Fetch state from device."""
        _LOGGER.debug("Polling TuneShine device state")
        try:
            state = await self.client.async_get_state()
        except TuneshineConnectionError as err:
            _LOGGER.debug("Connection error polling TuneShine: %s", err)
            raise UpdateFailed(
                f"Error communicating with TuneShine device: {err}"
            ) from err

        _LOGGER.debug(
            "Polled state: hardware_id=%s name=%r brightness=%s animation=%s",
            state.hardware_id,
            state.name,
            state.brightness,
            state.animation,
        )

        # Keep the config entry title in sync with the device name.
        current_name = state.name or state.hardware_id
        if self._entry.title != current_name:
            _LOGGER.debug(
                "Updating config entry title: %r -> %r", self._entry.title, current_name
            )
            self.hass.config_entries.async_update_entry(
                self._entry,
                title=current_name,
                data={**self._entry.data, CONF_DEVICE_NAME: current_name},
            )

        return state

    async def async_setup_source_entity(self, entity_id: str | None) -> None:
        """Set up or replace the source media player subscription in-place."""
        _LOGGER.debug("async_setup_source_entity called with entity_id=%r", entity_id)
        # Tear down any existing subscription first.
        if self._debounce_unsub is not None:
            _LOGGER.debug("Cancelling pending debounce timer")
            self._debounce_unsub()
            self._debounce_unsub = None
        if self._source_unsub is not None:
            _LOGGER.debug("Unsubscribing from previous source state listener")
            self._source_unsub()
            self._source_unsub = None

        if not entity_id:
            _LOGGER.debug("No source entity configured; source following disabled")
            return

        _LOGGER.debug("Setting up source listener for %s", entity_id)

        # Fire immediately to catch "already playing" at startup or on switch.
        state = self.hass.states.get(entity_id)
        _LOGGER.debug(
            "Initial state of %s: %s", entity_id, state.state if state else "not found"
        )
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
            # Cancel any pending debounced call before scheduling a new one.
            if self._debounce_unsub is not None:
                _LOGGER.debug("Cancelling previous debounce before rescheduling")
                self._debounce_unsub()
                self._debounce_unsub = None

            # Use a longer debounce for non-playing states to absorb the brief
            # idle flash that some players (e.g. HomePod) emit during track changes.
            is_playing = new_state is not None and new_state.state not in _NON_PLAYING_STATES
            delay = 0.5 if is_playing else 2.0
            _LOGGER.debug("Scheduling debounced handler (%.1f s, state=%s)", delay, new_state.state if new_state else "none")

            @callback
            def _debounced(_now: object) -> None:  # noqa: ARG001
                _LOGGER.debug("Debounce timer fired for %s", entity_id)
                self._debounce_unsub = None
                if self._handler_task is not None and not self._handler_task.done():
                    _LOGGER.debug("Cancelling in-flight handler task")
                    self._handler_task.cancel()
                self._handler_task = self.hass.async_create_task(
                    self._async_handle_source_state(new_state)
                )

            self._debounce_unsub = async_call_later(self.hass, delay, _debounced)

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
        _LOGGER.debug("Cleaning up source listener")
        if self._handler_task is not None and not self._handler_task.done():
            _LOGGER.debug("Cancelling in-flight handler task during cleanup")
            self._handler_task.cancel()
        self._handler_task = None
        if self._debounce_unsub is not None:
            _LOGGER.debug("Cancelling pending debounce timer during cleanup")
            self._debounce_unsub()
            self._debounce_unsub = None
        if self._source_unsub is not None:
            _LOGGER.debug("Unsubscribing from source state listener during cleanup")
            self._source_unsub()
            self._source_unsub = None

    async def _async_handle_source_state(self, state: State | None) -> None:
        """Send or clear image based on the source media player's state."""
        # Re-read the current state before acting: the debounced snapshot may be stale
        # if the source recovered to playing in the brief window between the debounce
        # firing and this coroutine actually running (e.g. a transient idle flash).
        if state is not None and state.state in _NON_PLAYING_STATES:
            current = self.hass.states.get(state.entity_id)
            if current is not None and current.state not in _NON_PLAYING_STATES:
                _LOGGER.debug(
                    "State recovered from %s to %s since debounce; using current state",
                    state.state,
                    current.state,
                )
                state = current

        _LOGGER.debug(
            "_async_handle_source_state: state=%s",
            state.state if state else "None",
        )
        try:
            if state is None or state.state in _NON_PLAYING_STATES:
                _LOGGER.debug(
                    "Source player not playing (%s), clearing image",
                    state.state if state else "none",
                )
                await self.client.async_clear_image()
            elif state.state == "playing":
                image_url = self._get_image_url(state)
                _LOGGER.debug(
                    "Source playing — track=%r artist=%r album=%r image_url=%s",
                    state.attributes.get("media_title"),
                    state.attributes.get("media_artist"),
                    state.attributes.get("media_album_name"),
                    image_url,
                )
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
            else:
                _LOGGER.debug("Source player in unhandled state %r, no action taken", state.state)
        except TuneshineApiError as err:
            _LOGGER.warning("Failed to update TuneShine from source player: %s", err)
            return
        _LOGGER.debug("Requesting coordinator refresh after source state handling")
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
        _LOGGER.debug(
            "_get_image_url: entity_picture_local=%r entity_picture=%r resolved=%r",
            state.attributes.get("entity_picture_local"),
            state.attributes.get("entity_picture"),
            entity_picture,
        )
        if not entity_picture:
            _LOGGER.debug("_get_image_url: no entity_picture found, returning None")
            return None

        try:
            base = get_url(self.hass, allow_internal=True, allow_ip=True)
        except NoURLAvailableError:
            _LOGGER.debug("_get_image_url: no HA URL available, using empty base")
            base = ""

        _LOGGER.debug("_get_image_url: HA base URL=%r", base)

        # Relative path — already the HA proxy endpoint.
        if entity_picture.startswith("/"):
            url = f"{base}{entity_picture}"
            _LOGGER.debug("_get_image_url: relative path -> %s", url)
            return url

        # Plain http:// — acceptable as-is.
        if entity_picture.startswith("http://"):
            _LOGGER.debug("_get_image_url: plain http URL -> %s", entity_picture)
            return entity_picture

        # https:// — TuneShine rejects https://.
        # Route through HA's media player proxy using the access_token.
        if entity_picture.startswith("https://"):
            access_token = state.attributes.get("access_token")
            if not access_token:
                _LOGGER.debug(
                    "_get_image_url: https:// URL but no access_token on %s, returning None",
                    state.entity_id,
                )
                return None
            url = f"{base}/api/media_player_proxy/{state.entity_id}?token={access_token}"
            _LOGGER.debug("_get_image_url: https proxied -> %s", url)
            return url

        _LOGGER.debug("_get_image_url: unrecognised URL scheme in %r, returning None", entity_picture)
        return None
