"""DataUpdateCoordinator for Tuneshine."""
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

import io

from .api import ImageMetadata, TuneshineApiClient, TuneshineApiError, TuneshineConnectionError, TuneshineState
from .const import CONF_DEVICE_NAME, CONF_SOURCE_ENTITY_ID, DOMAIN, POLL_INTERVAL_SECONDS, DisplayMode

_LOGGER = logging.getLogger(__name__)

_NON_PLAYING_STATES = frozenset({
    STATE_UNAVAILABLE, STATE_UNKNOWN, "off", "idle", "paused", "standby"
})


def _convert_to_webp(image_bytes: bytes) -> bytes:
    """Convert image bytes to WebP format (blocking — run in executor).

    The Tuneshine device only accepts WebP for multipart binary uploads.
    Sendspin sends JPEG, so this conversion is needed before uploading.
    """
    from PIL import Image  # noqa: PLC0415 — lazy import; Pillow is an HA dependency

    with Image.open(io.BytesIO(image_bytes)) as img:
        img = img.resize((64, 64), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="WEBP", lossless=True)
        return out.getvalue()


class TuneshineDataUpdateCoordinator(DataUpdateCoordinator[TuneshineState]):
    """Coordinator for a single Tuneshine device."""

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
        # Optimistic local metadata shown immediately after a send/clear command,
        # before the device's /state response has been updated.  Cleared on every
        # poll so real device data always wins once it arrives.
        self.optimistic_local_metadata: ImageMetadata | None = None
        self._last_remote_item_id: str | None = None
        # Sendspin state — set when a Sendspin server has added this client to a group.
        self._sendspin_active: bool = False
        self._sendspin_group_id: str | None = None
        self._sendspin_group_name: str | None = None
        self._sendspin_metadata: dict = {}
        # Incremented on each artwork upload; used as a cache-bust query parameter
        # when setting imageUrl on the device to http://{host}/artwork?v=N.
        self._sendspin_track_counter: int = 0
        # Last uploaded artwork bytes (WebP), retained across pause so we can
        # re-upload on resume without waiting for the server to resend.
        self._sendspin_cached_artwork: bytes | None = None

    async def _async_update_data(self) -> TuneshineState:
        """Fetch state from device."""
        _LOGGER.debug("Polling Tuneshine device state")
        try:
            state = await self.client.async_get_state()
        except TuneshineConnectionError as err:
            _LOGGER.debug("Connection error polling Tuneshine: %s", err)
            raise UpdateFailed(
                f"Error communicating with Tuneshine device: {err}"
            ) from err

        _LOGGER.debug(
            "Polled state: hardware_id=%s name=%r brightness=%s animation=%s"
            " local_err=%r remote_err=%r",
            state.hardware_id,
            state.name,
            state.brightness,
            state.animation,
            state.local_metadata.last_image_error if state.local_metadata else None,
            state.remote_metadata.last_image_error if state.remote_metadata else None,
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

        # Log when polling reveals a new remote track.
        remote = state.remote_metadata
        if remote and not remote.idle:
            if remote.item_id != self._last_remote_item_id:
                _LOGGER.debug(
                    "New remote track detected: track=%r artist=%r album=%r service=%r item_id=%r",
                    remote.track_name,
                    remote.artist_name,
                    remote.album_name,
                    remote.service_name,
                    remote.item_id,
                )
                self._last_remote_item_id = remote.item_id
        else:
            self._last_remote_item_id = None

        # Real device data has arrived — discard any optimistic state.
        # Exception: while Sendspin is active, keep the optimistic metadata so the
        # entity reflects the current Sendspin track between artwork uploads and polls.
        if not self._sendspin_active:
            self.optimistic_local_metadata = None
        return state

    # ------------------------------------------------------------------
    # Image operations with optimistic state updates
    # ------------------------------------------------------------------

    async def async_send_local_image(
        self,
        image_url: str,
        track_name: str | None = None,
        artist_name: str | None = None,
        album_name: str | None = None,
        service_name: str | None = None,
        animation: str | None = None,
    ) -> None:
        """POST /image and optimistically update entity state without touching coordinator data.

        coordinator.data always holds device-confirmed state.  The optimistic
        attribute is a side-channel that entities read until the next poll
        overwrites it with real device state.
        """
        await self.client.async_send_image(
            image_url,
            track_name=track_name,
            artist_name=artist_name,
            album_name=album_name,
            service_name=service_name,
            animation=animation,
        )
        self.optimistic_local_metadata = ImageMetadata(
            track_name=track_name,
            artist_name=artist_name,
            album_name=album_name,
            service_name=service_name,
            sub_service_name=None,
            item_id=None,
            zone_name=None,
            image_url=image_url,
            content_type=None,
            last_image_error=None,
            idle=False,
            account_name=None,
        )
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_clear_local_image(self) -> None:
        """DELETE /image and clear the optimistic local metadata immediately."""
        await self.client.async_clear_image()
        self.optimistic_local_metadata = None
        self.async_update_listeners()
        await self.async_request_refresh()

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
            # idle flash that some players emit during track changes.
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

    @property
    def sendspin_active(self) -> bool:
        """Return True when a Sendspin server has added this client to a group."""
        return self._sendspin_active

    @property
    def display_mode(self) -> DisplayMode:
        """Return how the display is currently being driven."""
        if self._sendspin_active:
            return DisplayMode.SENDSPIN
        local = self.optimistic_local_metadata or self.data.local_metadata
        if local and not local.idle:
            return DisplayMode.FOLLOWING if self.has_source else DisplayMode.LOCAL
        if self.data.remote_metadata and not self.data.remote_metadata.idle:
            return DisplayMode.REMOTE
        return DisplayMode.NONE

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
        # Sendspin takes over artwork delivery when active; ignore source changes.
        if self._sendspin_active:
            return
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
                await self.async_clear_local_image()
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
                    _LOGGER.debug("Source player has no image, clearing Tuneshine")
                    await self.async_clear_local_image()
                else:
                    _LOGGER.debug(
                        "Sending image to Tuneshine: url=%s track=%r artist=%r album=%r service=%r",
                        image_url,
                        state.attributes.get("media_title"),
                        state.attributes.get("media_artist"),
                        state.attributes.get("media_album_name"),
                        state.attributes.get("app_name") or "Home Assistant",
                    )
                    await self.async_send_local_image(
                        image_url=image_url,
                        track_name=state.attributes.get("media_title"),
                        artist_name=state.attributes.get("media_artist"),
                        album_name=state.attributes.get("media_album_name"),
                        service_name=state.attributes.get("app_name") or "Home Assistant",
                    )
            else:
                _LOGGER.debug("Source player in unhandled state %r, no action taken", state.state)
        except TuneshineApiError as err:
            _LOGGER.warning("Failed to update Tuneshine from source player: %s", err)
            return

    def _get_image_url(self, state: State) -> str | None:
        """Return an http:// image URL for the media player's current artwork.

        Tuneshine only accepts http:// URLs. Relative entity_picture paths
        (the HA proxy) and plain http:// URLs are used directly. https:// URLs
        (e.g. Spotify CDN, where media_image_remotely_accessible=True) are
        routed through HA's public media player proxy endpoint so Tuneshine
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

        # https:// — Tuneshine rejects https://.
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

    # ------------------------------------------------------------------
    # Sendspin callbacks — called by SendspinHandler when protocol events arrive
    # ------------------------------------------------------------------

    async def async_on_sendspin_group_joined(
        self, group_id: str, group_name: str | None
    ) -> None:
        """Handle the client being added to a Sendspin group."""
        _LOGGER.debug(
            "Sendspin group joined: group_id=%r group_name=%r", group_id, group_name
        )
        self._sendspin_active = True
        self._sendspin_group_id = group_id
        self._sendspin_group_name = group_name
        # Clear any stale optimistic state from source-following so it doesn't
        # bleed into Sendspin mode (stale HA proxy URLs are no longer valid).
        self.optimistic_local_metadata = None
        self.async_update_listeners()

    async def async_on_sendspin_group_left(self) -> None:
        """Handle the client being removed from a Sendspin group."""
        _LOGGER.debug("Sendspin group left — reverting to normal operation")
        self._sendspin_active = False
        self._sendspin_group_id = None
        self._sendspin_group_name = None
        self._sendspin_metadata = {}
        try:
            await self.async_clear_local_image()
        except TuneshineApiError as err:
            _LOGGER.warning("Failed to clear image after Sendspin group left: %s", err)
        # Re-trigger source following if a source media player is configured.
        source_entity_id = self._entry.options.get(CONF_SOURCE_ENTITY_ID)
        if source_entity_id:
            await self.async_setup_source_entity(source_entity_id)

    async def async_on_sendspin_metadata(self, metadata: dict) -> None:
        """Store incoming track metadata from the Sendspin server."""
        _LOGGER.debug(
            "Sendspin metadata: title=%r artist=%r album=%r",
            metadata.get("title"),
            metadata.get("artist"),
            metadata.get("album"),
        )
        self._sendspin_metadata = metadata
        # Update optimistic metadata immediately so title/artist/album reflect the
        # new track without waiting for artwork. Preserve the image URL only from
        # optimistic state (Sendspin-uploaded artwork) — data.local_metadata may
        # be stale from source-following and its proxy URLs are no longer valid.
        existing = self.optimistic_local_metadata
        self.optimistic_local_metadata = ImageMetadata(
            track_name=metadata.get("title"),
            artist_name=metadata.get("artist"),
            album_name=metadata.get("album"),
            service_name=self._sendspin_group_name,
            sub_service_name=None,
            item_id=None,
            zone_name=None,
            image_url=existing.image_url if existing else None,
            content_type="track",
            last_image_error=None,
            idle=False,
            account_name=None,
        )
        self.async_update_listeners()
        # Push metadata to the device immediately if a local image already exists
        # (from a previous Sendspin track). This keeps /state current without waiting
        # for artwork. The device returns 409 if no local image is set — that's fine,
        # artwork will carry the metadata when it arrives.
        if existing and existing.image_url:
            try:
                await self.client.async_update_metadata(
                    track_name=metadata.get("title"),
                    artist_name=metadata.get("artist"),
                    album_name=metadata.get("album"),
                    service_name=self._sendspin_group_name,
                )
            except TuneshineApiError as err:
                _LOGGER.debug("Sendspin metadata-only update skipped: %s", err)

    async def async_on_sendspin_artwork(self, image_bytes: bytes) -> None:
        """Upload incoming artwork binary to the device and update entity state."""
        self._sendspin_track_counter += 1
        # Build an http:// URL pointing at the device's own /artwork endpoint.
        # The ?v= parameter acts as a cache-bust so HA re-fetches on each track change.
        host_with_port = self.client._base_url.split("//", 1)[1]
        artwork_url = f"http://{host_with_port}/artwork?v={self._sendspin_track_counter}"
        meta = self._sendspin_metadata
        _LOGGER.debug(
            "Sendspin artwork received (%d bytes), converting to WebP — track=%r artist=%r",
            len(image_bytes),
            meta.get("title"),
            meta.get("artist"),
        )
        # The Tuneshine device only accepts WebP for binary (multipart) uploads.
        # Sendspin sends JPEG, so convert in a thread executor (Pillow is blocking).
        try:
            webp_bytes = await self.hass.async_add_executor_job(
                _convert_to_webp, image_bytes
            )
        except Exception as err:
            _LOGGER.warning("Failed to convert Sendspin artwork to WebP: %s", err)
            return
        _LOGGER.debug("Converted to WebP: %d bytes", len(webp_bytes))
        self._sendspin_cached_artwork = webp_bytes
        try:
            await self.client.async_send_image_binary(
                image_bytes=webp_bytes,
                # Do not pass image_url — the device only stores the binary for
                # GET /artwork when no URL is included in the multipart upload.
                track_name=meta.get("title"),
                artist_name=meta.get("artist"),
                album_name=meta.get("album"),
                service_name=self._sendspin_group_name,
            )
        except TuneshineApiError as err:
            _LOGGER.warning("Failed to upload Sendspin artwork: %s", err)
            return
        self.optimistic_local_metadata = ImageMetadata(
            track_name=meta.get("title"),
            artist_name=meta.get("artist"),
            album_name=meta.get("album"),
            service_name=self._sendspin_group_name,
            sub_service_name=None,
            item_id=str(self._sendspin_track_counter),
            zone_name=None,
            image_url=artwork_url,
            content_type="track",
            last_image_error=None,
            idle=False,
            account_name=None,
        )
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_on_sendspin_stream_end(self) -> None:
        """Handle stream end — clear artwork and cache if Sendspin is still active."""
        if not self._sendspin_active:
            _LOGGER.debug("Sendspin stream/end ignored — not in a group")
            return
        _LOGGER.debug(
            "Sendspin stream/end — clearing device image, cache (%s bytes), and metadata",
            len(self._sendspin_cached_artwork) if self._sendspin_cached_artwork else "none",
        )
        self._sendspin_cached_artwork = None
        self._sendspin_metadata = {}
        try:
            await self.async_clear_local_image()
        except TuneshineApiError as err:
            _LOGGER.warning("Failed to clear image on Sendspin stream end: %s", err)

    async def async_on_sendspin_playback_stopped(self) -> None:
        """Handle playback paused — blank the device but keep cached artwork for resume."""
        if not self._sendspin_active:
            _LOGGER.debug("Sendspin playback stopped ignored — not in a group")
            return
        _LOGGER.debug(
            "Sendspin playback stopped — blanking device; cached artwork retained (%s bytes)",
            len(self._sendspin_cached_artwork) if self._sendspin_cached_artwork else "none",
        )
        self.optimistic_local_metadata = None
        self.async_update_listeners()
        try:
            await self.client.async_clear_image()
            _LOGGER.debug("Sendspin playback stopped — device blanked successfully")
        except TuneshineApiError as err:
            _LOGGER.warning("Failed to blank device on Sendspin playback stop: %s", err)

    async def async_on_sendspin_playback_resumed(self) -> None:
        """Handle playback resumed — re-upload cached artwork if available."""
        if not self._sendspin_active:
            _LOGGER.debug("Sendspin playback resumed ignored — not in a group")
            return
        if not self._sendspin_cached_artwork:
            _LOGGER.debug("Sendspin playback resumed — no cached artwork, nothing to restore")
            return
        self._sendspin_track_counter += 1
        host_with_port = self.client._base_url.split("//", 1)[1]
        artwork_url = f"http://{host_with_port}/artwork?v={self._sendspin_track_counter}"
        meta = self._sendspin_metadata
        _LOGGER.debug(
            "Sendspin playback resumed — re-uploading cached artwork: %d bytes, "
            "track=%r artist=%r album=%r counter=%d",
            len(self._sendspin_cached_artwork),
            meta.get("title"),
            meta.get("artist"),
            meta.get("album"),
            self._sendspin_track_counter,
        )
        try:
            await self.client.async_send_image_binary(
                image_bytes=self._sendspin_cached_artwork,
                track_name=meta.get("title"),
                artist_name=meta.get("artist"),
                album_name=meta.get("album"),
                service_name=self._sendspin_group_name,
            )
            _LOGGER.debug("Sendspin playback resumed — artwork re-uploaded, artwork_url=%s", artwork_url)
        except TuneshineApiError as err:
            _LOGGER.warning("Failed to restore artwork on Sendspin resume: %s", err)
            return
        self.optimistic_local_metadata = ImageMetadata(
            track_name=meta.get("title"),
            artist_name=meta.get("artist"),
            album_name=meta.get("album"),
            service_name=self._sendspin_group_name,
            sub_service_name=None,
            item_id=str(self._sendspin_track_counter),
            zone_name=None,
            image_url=artwork_url,
            content_type="track",
            last_image_error=None,
            idle=False,
            account_name=None,
        )
        self.async_update_listeners()
        await self.async_request_refresh()
