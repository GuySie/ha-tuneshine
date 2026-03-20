# ha-tuneshine
Home Assistant integration for the [Tuneshine](https://tuneshine.rocks) LED album art display.

## Features

- **Media player entity** — reflects the display state (playing, idle) and exposes current track, artist, album, and artwork
- **Source media player** — select any HA media player to mirror; Tuneshine automatically updates when the track changes and clears when playback stops
- **Sendspin client** — Supports the Sendspin protocol, displaying artwork and metadata
- **Display mode sensor** — reports what is currently driving the display (`remote`, `local`, `following`, `sendspin`, or `none`)
- **Brightness controls** — set active and idle brightness (1–100, disabled by default)
- **Entity services** — `send_image` and `clear_image` for automation use

## Manual Install

Copy the `tuneshine` folder inside `custom_components` to your Home Assistant's `custom_components` folder.
Restart Home Assistant after copying.

Your Tuneshine should be automatically discovered. If not, add the integration via **Settings → Devices & Services → Add Integration → Tuneshine** and enter your device's IP address.

The integration will expose a media player that will follow what is currently being displayed by your Tuneshine.

## Source Following

Instead of the normal method of operation, which relies on Tuneshine's cloud server to send new coverart to your Tuneshine, you can set the integration to follow a media player in Home Assistant.
After setup, go to the Tuneshine device page and set **Source Entity** to any media player in your system. Tuneshine will display the current track artwork whenever that player is playing, and clear the display when it stops.

## Sendspin

The integration supports the [Sendspin](https://www.sendspin-audio.com/) protocol, allowing Sendspin servers such as [Music Assistant](https://www.music-assistant.io/player-support/sendspin/) to send artwork and metadata directly to the display.

On startup, the integration advertises the device as a Sendspin client via mDNS (`_sendspin._tcp.local.`) and exposes a WebSocket endpoint on Home Assistant's HTTP server. When a Sendspin server adds the device to a group:

- Artwork is received over the Sendspin stream and pushed to the display
- Track, artist, and album metadata updates the media player entity in real time
- Source following is suspended for the duration
- The display mode sensor reports `sendspin`

When the device is removed from the group or the server disconnects, the display is cleared and normal operation resumes (including source following if configured).

Sendspin is discovered automatically by Music Assistant once the Sendspin provider is enabled in its settings.

## Display Mode Sensor

The **Display Mode** sensor reports what is currently driving the Tuneshine display:

| Value | Meaning |
|-------|---------|
| `remote` | A cloud or streaming service (e.g. Spotify) is sending artwork via the Tuneshine cloud |
| `local` | An image was sent via the `send_image` service |
| `following` | The integration is mirroring a Home Assistant media player |
| `sendspin` | A Sendspin server (e.g. Music Assistant) is controlling the display |
| `none` | The display is idle with no active source |

## Entity Services

### `tuneshine.send_image`

Display an image by URL on the device.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `image_url` | Yes | `http://` URL of the image to display |
| `track_name` | No | Track title |
| `artist_name` | No | Artist name |
| `album_name` | No | Album name |
| `service_name` | No | Service label (e.g. `Spotify`) |
| `animation` | No | Transition animation: `none`, `dissolve`, `crate`, `crate_to_idle`, `crate_from_idle` |

### `tuneshine.clear_image`

Remove the locally-provided image, returning the display to its idle state.

## API

This integration was built against Tuneshine firmware 2.3.2 and Tuneshine device API 1.0.0.

## Vibecoding

This integration was created for my personal use only, using Claude Code and based on the [Tuneshine API documentation](https://links.tuneshine.rocks/help#api). This code is made available as an example to others. I am not a developer and this is in no way intended to be an official integration you should rely on in production - it is very *"it works on my computer"*. If you do not trust AI-generated code, please do not install this integration.
