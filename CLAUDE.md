# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Home Assistant custom integration for the Tuneshine LED album art display. The device exposes a local HTTP API discovered via mDNS (`_tuneshine._tcp.local.`). The integration polls the device every 10 seconds and supports source-following (mirroring a HA media player's artwork).

There is no build system, test suite, or linter configured. Development means editing Python files and restarting Home Assistant.

## Deployment

Install by copying `custom_components/tuneshine/` to the HA instance's `custom_components/` folder. Restart HA. The device auto-discovers via zeroconf or can be added manually by IP.

Testing Python code changes requires a **full HA restart** (Settings → System → Restart) — Python caches imported modules, so the "Reload" option in the UI does not pick up `.py` file changes.

## Architecture

All integration code lives in `custom_components/tuneshine/`.

**Data flow:**
1. `coordinator.py` (`TuneshineDataUpdateCoordinator`) polls `/state` every 10 seconds
2. `api.py` (`TuneshineApiClient`) makes all HTTP calls; `TuneshineState` is the full parsed state
3. Entities in `media_player.py`, `sensor.py`, `number.py`, `select.py` read from coordinator data
4. `coordinator.display_mode` derives `DisplayMode` (FOLLOWING/LOCAL/REMOTE/SENDSPIN/NONE) from state
5. `sendspin.py` (`SendspinWebSocketView`, `SendspinHandler`) handles incoming Sendspin server connections

**State model:** The device has no simple playing/stopped flag. State is inferred from `localMetadata` (HA-sent images) vs `remoteMetadata` (cloud/streaming). The media player entity reports `PLAYING` when `display_mode` is FOLLOWING, REMOTE, or SENDSPIN, otherwise `IDLE`.

**Source following:** When a source entity is configured, the coordinator subscribes to its state changes and sends the artwork URL to the device. Track changes are debounced (0.5s for playing, 2.0s for paused) to absorb brief idle flashes during track transitions. Image URLs are normalized to HTTP — relative paths get HA base URL prepended, HTTPS URLs are proxied through HA's media player proxy. Source following is suspended while Sendspin is active.

**Sendspin:** The integration advertises itself as a `_sendspin._tcp.local.` mDNS service per device, pointing at a HA WebSocket endpoint at `/api/sendspin/{hardware_id}`. When a Sendspin server (e.g. Music Assistant) connects, it sends artwork (JPEG) and metadata via the protocol. The integration converts incoming JPEG to 64×64 WebP and uploads it to the device via `POST /image` multipart. Artwork bytes are cached for resume. See `sendspin.py` and `.claude/sendspin_reference.md` for full details.

**Optimistic updates:** `async_send_local_image()` and `async_clear_local_image()` update coordinator state immediately before device confirmation, then real state overwrites on next poll. In Sendspin mode, `optimistic_local_metadata` is kept across polls to reflect the current Sendspin track.

**`always_update=False`** on the coordinator combined with `__eq__` on dataclasses means entity listeners only fire on actual state changes despite 10s polling.

## Key Design Decisions

- `_attr_name = None` on the media player → entity name equals device name with no "Media Player" suffix
- Brightness entities are `entity_registry_enabled_default=False` (disabled by default)
- Entity services (`send_image`, `clear_image`) registered via `platform.async_register_entity_service` in `media_player.py`
- Always use IPv4: `str(discovery_info.ip_address)` not `.local` hostnames (avoids IPv6 slow-path)
- `.local` hostnames in manual config are resolved to IPv4 at config flow time
- Sendspin WebSocket view is registered once (guarded by `_SENDSPIN_VIEW_REGISTERED`) even when multiple devices are configured
- Sendspin mDNS service name includes hardware_id for per-device uniqueness: `{device_name} ({hardware_id})._sendspin._tcp.local.`
- `media_image_remotely_accessible = False` when Sendspin is active — device artwork URL is local HTTP, which HA must proxy to avoid mixed-content blocks in the HTTPS frontend
- Sendspin artwork is uploaded without `imageUrl` in the multipart metadata so the device stores the binary at `GET /artwork` (see api_reference.md Known Spec Inaccuracies)
- Declare `media_width/height: 512` in `client/hello` so the Sendspin server sends a higher-resolution image; integration then resizes to 64×64 before uploading to device
- `stream/clear` is treated identically to `stream/end` (discards cached artwork and clears device)

## API Reference

See `.claude/api_reference.md` (not committed) for the Tuneshine local HTTP API spec, known spec inaccuracies vs actual device behavior, and sample responses. Built against firmware 2.3.2.

Key endpoints: `GET /health`, `GET /state`, `POST /image`, `DELETE /image`, `POST /brightness`.

See `.claude/sendspin_reference.md` (not committed) for the Sendspin protocol spec, including message types, delta semantics, binary message structure, mDNS/WebSocket endpoint details, and Tuneshine-specific implementation notes.
