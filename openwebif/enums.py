"""Enums in regard to OpenWebIf."""

from enum import IntEnum, StrEnum


class PlaybackType(IntEnum):
    """Enum for playback type."""

    live = 1
    recording = 2
    none = 3


class MessageType(IntEnum):
    """Enum for message type."""

    YESNO = 0
    INFO = 1
    WARNING = 2
    ERROR = 3


class PowerState(IntEnum):
    """Enum for power state."""

    TOGGLE_STANDBY = 0
    DEEP_STANDBY = 1
    REBOOT = 2
    RESTART_ENIGMA = 3
    WAKEUP = 4
    STANDBY = 5


class RemoteControlCodes(IntEnum):
    """Enum for remote control codes."""

    CHANNEL_UP = 402
    CHANNEL_DOWN = 403
    STOP = 128
    PLAY = 207
    PAUSE = 119


class ScreenGrabMode(StrEnum):
    """Enum for screen grab modes."""

    ALL = "all"
    OSD = "osd"
    VIDEO = "video"
    PIP = "pip"
    LCD = "lcd"


class ScreenGrabFormat(StrEnum):
    """Enum for screen grab formats."""

    JPG = "jpg"
    PNG = "png"
    BMP = "bmp"


class SetVolumeOption(StrEnum):
    """Enum for volume options."""

    UP = "up"
    DOWN = "down"
    MUTE = "mute"
