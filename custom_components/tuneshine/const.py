"""Constants for the Tuneshine integration."""
from enum import StrEnum


class DisplayMode(StrEnum):
    """Possible states for the Tuneshine display mode sensor."""

    MIRRORING = "mirroring"
    LOCAL = "local"
    REMOTE = "remote"
    SENDSPIN = "sendspin"
    NONE = "none"


DOMAIN = "tuneshine"
MANUFACTURER = "Tuneshine"
DEFAULT_PORT = 80

CONF_DEVICE_NAME = "device_name"
CONF_SOURCE_ENTITY_ID = "source_entity_id"
CONF_INPUT_MODE = "input_mode"

INPUT_MODE_SOURCE = "source_mirroring"
INPUT_MODE_SENDSPIN = "sendspin"
INPUT_MODE_REMOTE = "remote_only"

# API paths (confirmed from OpenAPI spec)
API_PATH_HEALTH = "/health"
API_PATH_STATE = "/state"
API_PATH_IMAGE = "/image"
API_PATH_BRIGHTNESS = "/brightness"
API_PATH_ARTWORK = "/artwork"

# Poll every 10s — local device; always_update=False skips callbacks when data is unchanged.
POLL_INTERVAL_SECONDS = 10

ANIMATIONS = ["none", "dissolve", "crate", "crate_to_idle", "crate_from_idle"]

SERVICE_SEND_IMAGE = "send_image"
SERVICE_CLEAR_IMAGE = "clear_image"

ATTR_IMAGE_URL = "image_url"
ATTR_TRACK_NAME = "track_name"
ATTR_ARTIST_NAME = "artist_name"
ATTR_ALBUM_NAME = "album_name"
ATTR_SERVICE_NAME = "service_name"
ATTR_ANIMATION = "animation"
