"""Tuneshine media player entity."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)

from .api import ImageMetadata
from .const import (
    ANIMATIONS,
    ATTR_ALBUM_NAME,
    ATTR_ANIMATION,
    ATTR_ARTIST_NAME,
    ATTR_IMAGE_URL,
    ATTR_SERVICE_NAME,
    ATTR_TRACK_NAME,
    DisplayMode,
    SERVICE_CLEAR_IMAGE,
    SERVICE_SEND_IMAGE,
)
from .coordinator import TuneshineDataUpdateCoordinator
from .entity import TuneshineConfigEntry, TuneshineEntity

# Map Tuneshine contentType strings to HA MediaType enum values.
_CONTENT_TYPE_MAP: dict[str, MediaType] = {
    "track": MediaType.MUSIC,
    "podcast": MediaType.PODCAST,
    "audiobook": MediaType.PODCAST,
    "radio": MediaType.MUSIC,
    "video": MediaType.VIDEO,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuneshineConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuneshine media player from a config entry."""
    coordinator: TuneshineDataUpdateCoordinator = entry.runtime_data
    async_add_entities([TuneshineMediaPlayer(coordinator)])

    # Register entity services on the platform so they support all HA
    # targeting methods (entity, device, area, floor, label).
    platform = async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SEND_IMAGE,
        {
            vol.Required(ATTR_IMAGE_URL): cv.url,
            vol.Optional(ATTR_TRACK_NAME): cv.string,
            vol.Optional(ATTR_ARTIST_NAME): cv.string,
            vol.Optional(ATTR_ALBUM_NAME): cv.string,
            vol.Optional(ATTR_SERVICE_NAME): cv.string,
            vol.Optional(ATTR_ANIMATION): vol.In(ANIMATIONS),
        },
        "async_send_image",
    )
    platform.async_register_entity_service(
        SERVICE_CLEAR_IMAGE,
        {},
        "async_clear_image",
    )


class TuneshineMediaPlayer(TuneshineEntity, MediaPlayerEntity):
    """Representation of a Tuneshine LED display as a media player."""

    # No suffix — entity name is just the device name ("Tuneshine").
    _attr_name = None
    _attr_supported_features = MediaPlayerEntityFeature.PLAY_MEDIA

    def __init__(self, coordinator: TuneshineDataUpdateCoordinator) -> None:
        """Initialise the media player."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.hardware_id}_player"

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState:
        """Return the playback state."""
        mode = self.coordinator.display_mode
        if mode == DisplayMode.SENDSPIN:
            # Sendspin is connected but may not be actively streaming.
            # Only report PLAYING when there is active artwork/metadata to show.
            return MediaPlayerState.PLAYING if self._active_metadata() else MediaPlayerState.IDLE
        if mode in (DisplayMode.FOLLOWING, DisplayMode.REMOTE):
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    # ------------------------------------------------------------------
    # Media attributes — prefer remote (streaming service) over local (HA).
    # ------------------------------------------------------------------

    def _active_metadata(self) -> ImageMetadata | None:
        """Return the most relevant metadata to surface."""
        data = self.coordinator.data
        # When Sendspin is driving the display, local (Sendspin-uploaded) metadata
        # takes priority over any concurrent remote track from the Tuneshine cloud.
        if self.coordinator.sendspin_active:
            # In Sendspin mode, only use optimistic_local_metadata — data.local_metadata
            # may be stale from a prior source-following session and its image URLs
            # (e.g. HA media proxy tokens) are no longer valid.
            local = self.coordinator.optimistic_local_metadata
            if local and not local.idle:
                return local
            return None
        remote = data.remote_metadata
        if remote and not remote.idle:
            return remote
        local = self.coordinator.optimistic_local_metadata or data.local_metadata
        if local and not local.idle:
            return local
        return None

    @property
    def media_title(self) -> str | None:
        """Return the current track title."""
        meta = self._active_metadata()
        return meta.track_name if meta else None

    @property
    def media_artist(self) -> str | None:
        """Return the current artist."""
        meta = self._active_metadata()
        return meta.artist_name if meta else None

    @property
    def media_album_name(self) -> str | None:
        """Return the current album name."""
        meta = self._active_metadata()
        return meta.album_name if meta else None

    @property
    def media_image_url(self) -> str | None:
        """Return the URL of the currently displayed image."""
        meta = self._active_metadata()
        return meta.image_url if meta else None

    @property
    def media_image_remotely_accessible(self) -> bool:
        """Return whether HA can embed the image URL directly in the frontend.

        Sendspin artwork is served from the local device (http://{device}/artwork),
        which is a local HTTP URL. Setting False causes HA to proxy it server-side,
        avoiding mixed-content blocks when HA is served over HTTPS.
        """
        return not self.coordinator.sendspin_active

    @property
    def media_content_type(self) -> MediaType:
        """Return the content type of the currently displayed media."""
        meta = self._active_metadata()
        if meta and meta.content_type:
            return _CONTENT_TYPE_MAP.get(meta.content_type, MediaType.MUSIC)
        return MediaType.MUSIC

    @property
    def source(self) -> str | None:
        """Return the connected streaming service name."""
        data = self.coordinator.data
        if data.remote_metadata:
            return data.remote_metadata.service_name
        return None

    @property
    def source_list(self) -> list[str] | None:
        """Return the current streaming service as the only source."""
        source = self.source
        return [source] if source else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for automations."""
        data = self.coordinator.data
        attrs: dict[str, Any] = {}
        if data.remote_metadata:
            if data.remote_metadata.item_id:
                attrs["item_id"] = data.remote_metadata.item_id
            if data.remote_metadata.zone_name:
                attrs["zone_name"] = data.remote_metadata.zone_name
            if data.remote_metadata.sub_service_name:
                attrs["sub_service"] = data.remote_metadata.sub_service_name
            if data.remote_metadata.account_name:
                attrs["account_name"] = data.remote_metadata.account_name
        return attrs

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def async_play_media(
        self,
        media_type: str,
        media_id: str,
        **kwargs: Any,
    ) -> None:
        """Handle play_media — send media_id as an image URL."""
        await self.coordinator.async_send_local_image(
            media_id, service_name="Home Assistant"
        )

    async def async_send_image(
        self,
        image_url: str,
        track_name: str | None = None,
        artist_name: str | None = None,
        album_name: str | None = None,
        service_name: str | None = None,
        animation: str | None = None,
    ) -> None:
        """Send an image URL to display on the device (entity service handler)."""
        await self.coordinator.async_send_local_image(
            image_url,
            track_name=track_name,
            artist_name=artist_name,
            album_name=album_name,
            service_name=service_name or "Home Assistant",
            animation=animation,
        )

    async def async_clear_image(self) -> None:
        """Remove the locally-provided image (entity service handler)."""
        await self.coordinator.async_clear_local_image()
