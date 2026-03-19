# ha-tuneshine
Home Assistant integration for the [TuneShine](https://tuneshine.rocks) LED album art display.

## Features

- **Media player entity** — reflects the display state (playing, idle, standby) and exposes current track, artist, album, and artwork
- **Source media player** — select any HA media player to mirror; TuneShine automatically updates when the track changes and clears when playback stops
- **Brightness controls** — set active and idle brightness (1–100, disabled by default)
- **Entity services** — `send_image` and `clear_image` for automation use

## Manual Install

Copy the `tuneshine` folder inside `custom_components` to your Home Assistant's `custom_components` folder.
Restart Home Assistant after copying.

Your TuneShine should be automatically discovered. If not, add the integration via **Settings → Devices & Services → Add Integration → TuneShine** and enter your device's IP address.

The integration will expose a media player that will follow what is currently being displayed by your TuneShine.

## Source Following

Instead of the normal method of operation, which relies on TuneShine's cloud server to send new coverart to your TuneShine, you can set the integration to follow a media player in Home Assistant.  
After setup, go to the TuneShine device page and set **Source Entity** to any media player in your system. TuneShine will display the current track artwork whenever that player is playing, and clear the display when it stops.

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

## Vibecoding

This integration was created using Claude Code, based on the [TuneShine API documentation](https://links.tuneshine.rocks/help#api). If you do not trust AI-generated code, please do not install this integration.
