"""TuneShine media player entity."""
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
    SERVICE_CLEAR_IMAGE,
    SERVICE_SEND_IMAGE,
)
from .coordinator import TuneshineDataUpdateCoordinator
from .entity import TuneshineConfigEntry, TuneshineEntity

# Map TuneShine contentType strings to HA MediaType enum values.
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
    """Set up TuneShine media player from a config entry."""
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
    """Representation of a TuneShine LED display as a media player."""

    # No suffix — entity name is just the device name ("TuneShine").
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
        data = self.coordinator.data
        if data.local_metadata is not None and not data.local_metadata.idle:
            # HA has sent a custom image — treat as IDLE (not music mode).
            return MediaPlayerState.IDLE
        if data.remote_metadata is not None:
            if not data.remote_metadata.idle:
                return MediaPlayerState.PLAYING
            return MediaPlayerState.STANDBY
        return MediaPlayerState.STANDBY

    # ------------------------------------------------------------------
    # Media attributes — prefer remote (streaming service) over local (HA).
    # ------------------------------------------------------------------

    def _active_metadata(self) -> ImageMetadata | None:
        """Return the most relevant metadata to surface."""
        data = self.coordinator.data
        remote = data.remote_metadata
        if remote and not remote.idle:
            return remote
        if data.local_metadata and not data.local_metadata.idle:
            return data.local_metadata
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
        """Return True — artwork URLs come from streaming service CDNs."""
        return True

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
        if data.local_metadata and not data.local_metadata.idle:
            display_mode = "local"
        elif data.remote_metadata and not data.remote_metadata.idle:
            display_mode = "remote"
        else:
            display_mode = "none"

        attrs: dict[str, Any] = {"display_mode": display_mode}
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
        await self.coordinator.client.async_send_image(
            media_id, service_name="Home Assistant"
        )
        await self.coordinator.async_request_refresh()

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
        await self.coordinator.client.async_send_image(
            image_url,
            track_name=track_name,
            artist_name=artist_name,
            album_name=album_name,
            service_name=service_name or "Home Assistant",
            animation=animation,
        )
        await self.coordinator.async_request_refresh()

    async def async_clear_image(self) -> None:
        """Remove the locally-provided image (entity service handler)."""
        await self.coordinator.client.async_clear_image()
        await self.coordinator.async_request_refresh()
