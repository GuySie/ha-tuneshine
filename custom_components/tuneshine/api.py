"""Tuneshine local HTTP API client."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import aiohttp

_LOGGER = logging.getLogger(__name__)

from .const import (
    API_PATH_BRIGHTNESS,
    API_PATH_HEALTH,
    API_PATH_IMAGE,
    API_PATH_STATE,
    DEFAULT_PORT,
)


class TuneshineApiError(Exception):
    """Base exception for Tuneshine API errors."""


class TuneshineConnectionError(TuneshineApiError):
    """Raised when network or timeout errors occur."""


@dataclass
class ImageMetadata:
    """Metadata for an image displayed on the device."""

    track_name: str | None
    artist_name: str | None
    album_name: str | None
    service_name: str | None
    sub_service_name: str | None
    item_id: str | None
    zone_name: str | None
    image_url: str | None
    content_type: str | None
    last_image_error: str | None
    idle: bool
    account_name: str | None


@dataclass
class BrightnessConfig:
    """Device brightness configuration."""

    base: int
    active: int
    idle: int


@dataclass
class TuneshineState:
    """Full device state from GET /state."""

    hardware_id: str
    name: str | None
    firmware_version: str
    brightness: BrightnessConfig
    animation: str
    local_metadata: ImageMetadata | None
    remote_metadata: ImageMetadata | None


def _parse_image_metadata(data: dict | None) -> ImageMetadata | None:
    """Parse an ImageMetadata object from a raw dict, or return None."""
    if data is None:
        return None
    return ImageMetadata(
        track_name=data.get("trackName"),
        artist_name=data.get("artistName"),
        album_name=data.get("albumName"),
        service_name=data.get("serviceName"),
        sub_service_name=data.get("subServiceName"),
        item_id=data.get("itemId"),
        zone_name=data.get("zoneName"),
        image_url=data.get("imageUrl"),
        content_type=data.get("contentType"),
        last_image_error=data.get("lastImageError"),
        idle=data.get("idle", False),
        account_name=data.get("accountName"),
    )


def _parse_state(data: dict) -> TuneshineState:
    """Parse a TuneshineState from a raw /state response dict."""
    config = data.get("config", {})
    brightness_data = config.get("brightness", {})
    return TuneshineState(
        hardware_id=data["hardwareId"],
        name=data.get("name"),
        firmware_version=data.get("firmwareVersion", ""),
        brightness=BrightnessConfig(
            base=brightness_data.get("base", 50),
            active=brightness_data.get("active", 50),
            idle=brightness_data.get("idle", 50),
        ),
        animation=config.get("animation", "none"),
        local_metadata=_parse_image_metadata(data.get("localMetadata")),
        remote_metadata=_parse_image_metadata(data.get("remoteMetadata")),
    )


def _metadata_fields(
    track_name: str | None,
    artist_name: str | None,
    album_name: str | None,
    service_name: str | None,
) -> dict[str, str]:
    """Return a dict of non-None metadata fields for the device API."""
    fields = {
        "trackName": track_name,
        "artistName": artist_name,
        "albumName": album_name,
        "serviceName": service_name,
    }
    return {k: v for k, v in fields.items() if v is not None}


class TuneshineApiClient:
    """Async HTTP client for the Tuneshine local API."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialise the client."""
        self._base_url = f"http://{host}:{DEFAULT_PORT}"
        self._session = session

    @property
    def base_url(self) -> str:
        """Return the base URL for the device (http://host:port)."""
        return self._base_url

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: object,
    ) -> dict:
        """Make an HTTP request, returning parsed JSON."""
        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                timeout=aiohttp.ClientTimeout(total=10),
                **kwargs,
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise TuneshineApiError(
                        f"HTTP {response.status} from {path}: {body}"
                    )
                if response.content_type == "application/json":
                    raw = await response.read()
                    return json.loads(raw.decode("utf-8", errors="replace"))
                return {}
        except aiohttp.ClientError as err:
            raise TuneshineConnectionError(
                f"Connection error communicating with Tuneshine: {err}"
            ) from err
        except TimeoutError as err:
            raise TuneshineConnectionError(
                f"Timeout communicating with Tuneshine at {self._base_url}"
            ) from err

    async def async_health_check(self) -> None:
        """GET /health — raises TuneshineConnectionError if unreachable."""
        await self._request("GET", API_PATH_HEALTH)

    async def async_get_state(self) -> TuneshineState:
        """GET /state — returns parsed TuneshineState."""
        data = await self._request("GET", API_PATH_STATE)
        return _parse_state(data)

    async def async_send_image(
        self,
        image_url: str,
        track_name: str | None = None,
        artist_name: str | None = None,
        album_name: str | None = None,
        service_name: str | None = None,
        animation: str | None = None,
    ) -> None:
        """POST /image — display an image by URL with optional metadata."""
        _LOGGER.debug(
            "POST /image (url): %s track=%r artist=%r album=%r service=%r animation=%r",
            image_url, track_name, artist_name, album_name, service_name, animation,
        )
        body: dict[str, object] = {"imageUrl": image_url, **_metadata_fields(track_name, artist_name, album_name, service_name)}
        if animation is not None:
            body["animation"] = animation
        await self._request("POST", API_PATH_IMAGE, json=body)

    async def async_update_metadata(
        self,
        track_name: str | None = None,
        artist_name: str | None = None,
        album_name: str | None = None,
        service_name: str | None = None,
    ) -> None:
        """POST /image with no imageUrl — update metadata on the existing local image.

        Raises TuneshineApiError with status 409 if no local image is currently set.
        """
        _LOGGER.debug(
            "POST /image (metadata only): track=%r artist=%r album=%r service=%r",
            track_name, artist_name, album_name, service_name,
        )
        body = _metadata_fields(track_name, artist_name, album_name, service_name)
        await self._request("POST", API_PATH_IMAGE, json=body)

    async def async_clear_image(self) -> None:
        """DELETE /image — remove locally-provided image."""
        _LOGGER.debug("DELETE /image")
        await self._request("DELETE", API_PATH_IMAGE)

    async def async_send_image_binary(
        self,
        image_bytes: bytes,
        image_url: str | None = None,
        track_name: str | None = None,
        artist_name: str | None = None,
        album_name: str | None = None,
        service_name: str | None = None,
    ) -> None:
        """POST /image with multipart/form-data — upload binary image directly."""
        metadata = _metadata_fields(track_name, artist_name, album_name, service_name)
        if image_url is not None:
            metadata["imageUrl"] = image_url
        form = aiohttp.FormData()
        form.add_field("image", image_bytes, content_type="image/webp")
        if metadata:
            form.add_field("metadata", json.dumps(metadata), content_type="application/json")
        _LOGGER.debug(
            "POST /image (binary): %d bytes, metadata=%r", len(image_bytes), metadata or None
        )
        await self._request("POST", API_PATH_IMAGE, data=form)

    async def async_set_brightness(
        self,
        active: int | None = None,
        idle: int | None = None,
    ) -> None:
        """POST /brightness — set active and/or idle brightness (1–100)."""
        if active is None and idle is None:
            raise ValueError("At least one of active or idle must be provided")
        _LOGGER.debug("POST /brightness: active=%r idle=%r", active, idle)
        body: dict[str, int] = {}
        if active is not None:
            body["active"] = active
        if idle is not None:
            body["idle"] = idle
        await self._request("POST", API_PATH_BRIGHTNESS, json=body)
