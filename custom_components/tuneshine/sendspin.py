"""Sendspin protocol client for Tuneshine.

Implements a subset of the Sendspin protocol sufficient for artwork display
and track metadata. The integration runs an HTTP WebSocket endpoint that
Sendspin servers (e.g. Music Assistant) connect to after discovering the
client via mDNS (_sendspin._tcp.local.).

Binary message format: 9-byte header ">Bq" (type: u8, timestamp_us: i64),
followed by the raw image payload (JPEG for ARTWORK_CHANNEL_0, type=8).
"""
from __future__ import annotations

import json
import logging
import struct
import time
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import TuneshineDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Binary message type identifier for artwork channel 0.
_ARTWORK_CHANNEL_0 = 8
# Sendspin binary header: big-endian unsigned byte + signed 64-bit int.
_BINARY_HEADER_FORMAT = ">Bq"
_BINARY_HEADER_SIZE = struct.calcsize(_BINARY_HEADER_FORMAT)


class SendspinWebSocketView(HomeAssistantView):
    """WebSocket endpoint for incoming Sendspin server connections."""

    url = "/api/sendspin/{hardware_id}"
    name = "api:sendspin:hardware_id"
    # Sendspin servers do not present HA auth tokens.
    requires_auth = False

    async def get(self, request: web.Request, hardware_id: str) -> web.WebSocketResponse:
        """Upgrade an incoming connection to a WebSocket and run the protocol."""
        hass: HomeAssistant = request.app["hass"]
        coordinators: dict = hass.data.get(DOMAIN, {})
        coordinator: TuneshineDataUpdateCoordinator | None = coordinators.get(hardware_id)

        if coordinator is None:
            _LOGGER.warning(
                "Sendspin connection for unknown hardware_id %r — ignoring", hardware_id
            )
            raise web.HTTPNotFound

        _LOGGER.debug(
            "Sendspin connection accepted from %s for hardware_id %r",
            request.remote,
            hardware_id,
        )
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        handler = SendspinHandler(coordinator, ws)
        await handler.run()
        return ws


class SendspinHandler:
    """Handles the Sendspin protocol over a single WebSocket connection."""

    def __init__(
        self,
        coordinator: TuneshineDataUpdateCoordinator,
        ws: web.WebSocketResponse,
    ) -> None:
        """Initialise the handler."""
        self._coordinator = coordinator
        self._ws = ws

    async def run(self) -> None:
        """Drive the connection: send hello, then read messages until close."""
        hardware_id = self._coordinator.data.hardware_id
        device_name = self._coordinator.data.name or "Tuneshine"
        _LOGGER.debug("Sendspin connection opened for device %r (%s)", device_name, hardware_id)

        await self._send_client_hello(hardware_id, device_name)

        try:
            async for msg in self._ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self._handle_text(msg.data)
                elif msg.type == web.WSMsgType.BINARY:
                    await self._handle_binary(msg.data)
                elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.CLOSING, web.WSMsgType.CLOSED):
                    break
                elif msg.type == web.WSMsgType.ERROR:
                    _LOGGER.warning(
                        "Sendspin WebSocket error for %s: %s", hardware_id, self._ws.exception()
                    )
                    break
        except Exception:
            _LOGGER.exception("Unexpected error in Sendspin handler for %s", hardware_id)
        finally:
            _LOGGER.debug("Sendspin connection closed for device %s", hardware_id)
            # If we were in a group when the connection dropped, leave gracefully.
            if self._coordinator.sendspin_active:
                _LOGGER.debug(
                    "Sendspin connection lost while in group — reverting to normal operation"
                )
                await self._coordinator.async_on_sendspin_group_left()

    async def _send_client_hello(self, hardware_id: str, device_name: str) -> None:
        """Send the client/hello handshake message."""
        msg = {
            "type": "client/hello",
            "payload": {
                "client_id": f"tuneshine-{hardware_id}",
                "name": device_name,
                "version": 1,
                "supported_roles": ["artwork@v1", "metadata@v1"],
                # JSON key is "artwork@v1_support" per the Alias in aiosendspin's model.
                "artwork@v1_support": {
                    "channels": [
                        {
                            "source": "album",
                            "format": "jpeg",
                            "media_width": 64,
                            "media_height": 64,
                        }
                    ]
                },
            },
        }
        await self._ws.send_str(json.dumps(msg))
        _LOGGER.debug("Sent client/hello for %s", hardware_id)

    async def _send_client_time(self) -> None:
        """Send client/time for clock synchronisation."""
        ts = time.time_ns() // 1000
        msg = {
            "type": "client/time",
            "payload": {"client_transmitted": ts},
        }
        await self._ws.send_str(json.dumps(msg))
        _LOGGER.debug("Sent client/time: client_transmitted=%d", ts)

    async def _handle_text(self, data: str) -> None:
        """Dispatch an incoming JSON text message by type."""
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            _LOGGER.debug("Sendspin: ignoring non-JSON text message")
            return

        msg_type = parsed.get("type")
        payload = parsed.get("payload", {})

        if msg_type == "server/hello":
            _LOGGER.debug(
                "Sendspin server/hello: server=%r active_roles=%r",
                payload.get("name"),
                payload.get("active_roles"),
            )
            await self._send_client_time()

        elif msg_type == "group/update":
            group_id = payload.get("group_id")
            group_name = payload.get("group_name")
            playback_state = payload.get("playback_state")
            _LOGGER.debug(
                "Sendspin group/update: group_id=%r group_name=%r playback_state=%r",
                group_id,
                group_name,
                playback_state,
            )
            if group_id is not None:
                await self._coordinator.async_on_sendspin_group_joined(group_id, group_name)
            else:
                await self._coordinator.async_on_sendspin_group_left()

        elif msg_type == "server/state":
            metadata = payload.get("metadata") or {}
            _LOGGER.debug(
                "Sendspin server/state: has_metadata=%s playback_state=%r",
                bool(metadata),
                payload.get("playback_state"),
            )
            if metadata:
                await self._coordinator.async_on_sendspin_metadata(metadata)

        elif msg_type == "stream/end":
            _LOGGER.debug("Sendspin stream/end received")
            await self._coordinator.async_on_sendspin_stream_end()

        else:
            _LOGGER.debug("Sendspin: ignoring message type %r", msg_type)

    async def _handle_binary(self, data: bytes) -> None:
        """Handle a binary message — extract artwork from channel 0."""
        if len(data) < _BINARY_HEADER_SIZE:
            _LOGGER.debug("Sendspin: binary message too short (%d bytes), ignoring", len(data))
            return

        msg_type, _timestamp_us = struct.unpack_from(_BINARY_HEADER_FORMAT, data)

        if msg_type == _ARTWORK_CHANNEL_0:
            image_bytes = data[_BINARY_HEADER_SIZE:]
            if image_bytes:
                _LOGGER.debug(
                    "Sendspin: received artwork (%d bytes)", len(image_bytes)
                )
                await self._coordinator.async_on_sendspin_artwork(image_bytes)
        else:
            _LOGGER.debug("Sendspin: ignoring binary message type %d", msg_type)
