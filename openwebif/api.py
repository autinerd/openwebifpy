"""API for communicating with OpenWebIf."""

from __future__ import annotations

import logging
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from re import sub
from time import time
from typing import TYPE_CHECKING, Any, cast

import aiohttp
from yarl import URL

from .constants import (
    PATH_ABOUT,
    PATH_BOUQUETS,
    PATH_EPGNOW,
    PATH_GETALLSERVICES,
    PATH_GRAB,
    PATH_MESSAGE,
    PATH_POWERSTATE,
    PATH_REMOTECONTROL,
    PATH_STATUSINFO,
    PATH_VOL,
    PATH_ZAP,
)
from .enums import (
    MessageType,
    PlaybackType,
    PowerState,
    RemoteControlCodes,
    ScreenGrabFormat,
    ScreenGrabMode,
    SetVolumeOption,
    SType,
)
from .error import InvalidAuthError

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)


def enable_logging() -> None:
    """Set up the logging for home assistant."""
    logging.basicConfig(level=logging.INFO)


# pylint: disable=too-many-instance-attributes
@dataclass
class OpenWebIfServiceEvent:
    """Represent a OpenWebIf service event."""

    filename: str | None = None
    id: int | None = None
    name: str | None = None
    serviceref: str | None = None
    begin: str | None = None
    begin_timestamp: int | None = None
    end: str | None = None
    end_timestamp: int | None = None
    description: str | None = None
    fulldescription: str | None = None
    station: str | None = None


# pylint: disable=too-many-instance-attributes
@dataclass
class OpenWebIfStatus:
    """Repesent a OpenWebIf status."""

    currservice: OpenWebIfServiceEvent
    volume: int | None = None
    muted: bool | None = None
    in_standby: bool | None = False
    is_recording: bool | None = False
    streaming_list: str | None = None
    is_streaming: bool | None = False
    status_info: dict | None = None
    is_recording_playback: bool | None = False


# pylint: disable=too-many-instance-attributes, disable=too-many-public-methods
class OpenWebIfDevice:
    """Represent a OpenWebIf client device."""

    _session: aiohttp.ClientSession | None
    base: URL
    status: OpenWebIfStatus
    is_offline: bool = False
    turn_off_to_deep: bool
    picon_url: str | None = None
    source_bouquet: str | None = None
    mac_address: str | None = None
    sources: dict[str, Any]
    source_list: list[str]
    bouquet_list: OrderedDict[str, str]  # {bouquetname: bouquetref}

    def __init__(
        self,
        host: str | aiohttp.ClientSession,
        port: int = 80,
        username: str | None = None,
        password: str | None = None,
        is_https: bool = False,
        turn_off_to_deep: bool = False,
        source_bouquet: str | None = None,
    ) -> None:
        """Define an enigma2 device.

        :param host: IP or hostname or a ClientSession
        :param port: OpenWebif port
        :param username: e2 user
        :param password: e2 user password
        :param is_https: use https or not
        :param turn_off_to_deep: If True, send to deep standby on turn off
        :param source_bouquet: Which bouquet ref you want to load
        """
        enable_logging()

        if isinstance(host, str):
            _LOGGER.debug("Initialising new openwebif client for host: %s", host)
            self.base = URL.build(
                scheme="http" if not is_https else "https",
                host=host,
                port=port,
                user=username,
                password=password,
            )
            self._session = aiohttp.ClientSession(self.base)
        else:
            self.base = cast(URL, host._base_url)  # noqa: SLF001
            self._session = host
        self.turn_off_to_deep = turn_off_to_deep
        self.source_bouquet = source_bouquet
        self.status = OpenWebIfStatus(currservice=OpenWebIfServiceEvent())
        self.sources = {}
        self.source_list = []
        self.bouquet_list = OrderedDict()

    async def close(self) -> None:
        """Close the connection."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    def default_all(self) -> None:
        """Set all properties to default."""
        self.status = OpenWebIfStatus(currservice=OpenWebIfServiceEvent())

    async def get_about(self) -> dict[str, Any]:
        """Get general information."""
        return await self._call_api(PATH_ABOUT)

    async def get_status_info(self) -> dict[str, Any]:
        """Get status info."""
        return await self._call_api(PATH_STATUSINFO)

    async def update(self) -> None:
        """Refresh current state based from <host>/api/statusinfo."""
        self.status.status_info = await self._call_api(PATH_STATUSINFO)

        if self.is_offline or not self.status.status_info:
            self.default_all()
            return

        self.status.currservice.filename = self.status.status_info.get(
            "currservice_filename",
        )
        self.status.currservice.id = self.status.status_info.get("currservice_id")
        self.status.currservice.name = self.status.status_info.get("currservice_name")
        self.status.currservice.serviceref = self.status.status_info.get(
            "currservice_serviceref",
        )
        self.status.currservice.begin = self.status.status_info.get("currservice_begin")
        self.status.currservice.begin_timestamp = self.status.status_info.get(
            "currservice_begin_timestamp",
        )

        self.status.currservice.end = self.status.status_info.get("currservice_end")

        self.status.currservice.end_timestamp = self.status.status_info.get(
            "currservice_end_timestamp",
        )

        self.status.currservice.description = self.status.status_info.get(
            "currservice_description",
        )
        self.status.currservice.station = self.status.status_info.get(
            "currservice_station",
        )
        self.status.currservice.fulldescription = self.status.status_info.get(
            "currservice_fulldescription",
        )
        self.status.in_standby = self.status.status_info["inStandby"] == "true"
        self.status.is_recording = self.status.status_info["isRecording"] == "true"
        if "isStreaming" in self.status.status_info:
            self.status.is_streaming = self.status.status_info["isStreaming"] == "true"
        else:
            self.status.is_streaming = None
        self.status.muted = self.status.status_info.get("muted")
        self.status.volume = self.status.status_info.get("volume")

        if not self.sources:
            self.sources = await self.get_bouquet_sources(bouquet=self.source_bouquet)
            self.source_list = list(self.sources.keys())

        if self.get_current_playback_type() == PlaybackType.RECORDING:
            # try get correct channel name
            channel_name = self.get_channel_name_from_serviceref()
            self.status.status_info["currservice_station"] = channel_name
            self.status.currservice.station = channel_name
            self.status.currservice.name = f"🔴 {self.status.currservice.name}"

        if not self.status.in_standby:
            url = await self.get_current_playing_picon_url(
                channel_name=self.status.currservice.station,
                currservice_serviceref=self.status.currservice.serviceref,
            )
            self.picon_url = str(self.base.with_path(url)) if url is not None else None

    async def get_volume(self) -> int:
        """Get the current volume."""

        return int((await self._call_api(PATH_VOL))["current"])

    async def set_volume(self, new_volume: int | SetVolumeOption) -> bool:
        """Set the volume to the new value.

        :param new_volume: int from 0-100
        :return: True if successful, false if there was a problem
        """
        return self._check_response_result(
            await self._call_api(
                PATH_VOL,
                {
                    "set": ("set" + str(new_volume))
                    if isinstance(new_volume, int)
                    else str(new_volume),
                },
            ),
        )

    async def send_message(
        self,
        text: str,
        message_type: MessageType = MessageType.INFO,
        timeout: int = -1,
    ) -> bool:
        """Send a message to the TV screen.

        :param text: The message to display
        :param message_type: The type of message (0 = YES/NO, 1 = INFO, 2 = WARNING, 3 = ERROR)
        :return: True if successful, false if there was a problem
        """

        return self._check_response_result(
            await self._call_api(
                PATH_MESSAGE,
                {"timeout": timeout, "type": message_type.value, "text": text},
            ),
        )

    async def turn_on(self) -> bool:
        """Take the box out of standby."""

        if self.is_offline:
            _LOGGER.debug("Box is offline, going to try wake on lan")
            # self.wake_up()

        return self._check_response_result(
            await self._call_api(PATH_POWERSTATE, {"newstate": PowerState.WAKEUP}),
        )

    def get_screen_grab_url(
        self,
        mode: ScreenGrabMode = ScreenGrabMode.ALL,
        file_format: ScreenGrabFormat = ScreenGrabFormat.JPG,
        resolution: int = 0,
    ) -> URL:
        """Get the URL for a screen grab.

        :param mode: The screen grab mode
        :param file_format: The picture format
        :param resolution: The resolution to grab (0 = native resolution)
        :return: The URL for the screen grab
        """
        return self.base.with_path(PATH_GRAB).with_query(
            {
                "mode": mode.value,
                "format": file_format.value,
                "t": int(time()),
                "r": resolution,
            },
        )

    async def turn_off(self) -> bool:
        """Put the box out into standby."""
        if self.turn_off_to_deep:
            return await self.deep_standby()

        return self._check_response_result(
            await self._call_api(PATH_POWERSTATE, {"newstate": PowerState.STANDBY}),
        )

    async def deep_standby(self) -> bool:
        """Go into deep standby."""

        return self._check_response_result(
            await self._call_api(
                PATH_POWERSTATE,
                {"newstate": PowerState.DEEP_STANDBY},
            ),
        )

    async def set_powerstate(self, newstate: PowerState) -> bool:
        """Set a new power state."""

        return self._check_response_result(
            await self._call_api(PATH_POWERSTATE, {"newstate": newstate.value}),
        )

    async def send_remote_control_action(self, action: RemoteControlCodes) -> bool:
        """Send a remote control command."""

        return self._check_response_result(
            await self._call_api(PATH_REMOTECONTROL, {"command": action.value}),
        )

    async def toggle_mute(self) -> bool:
        """Send mute command."""
        response = await self._call_api(PATH_VOL, {"set": SetVolumeOption.MUTE})
        return False if response is None else bool(response["ismute"])

    @staticmethod
    def _check_response_result(response: dict[str, Any] | None) -> bool:
        """Check the result of the response.

        :param response:
        :return: Returns True if command success, else, False
        """
        return False if response is None else bool(response["result"])

    def is_currently_recording_playback(self) -> bool:
        """Return true if playing back recording."""
        return self.get_current_playback_type() == PlaybackType.RECORDING

    def get_current_playback_type(self) -> PlaybackType | None:
        """Get the currservice_serviceref playing media type.

        :return: PlaybackType.LIVE or PlaybackType.RECORDING
        """

        if self.status.currservice and self.status.currservice.serviceref:
            if self.status.currservice.serviceref.startswith("1:0:0"):
                # This is a recording, not a live channel
                return PlaybackType.RECORDING

            return PlaybackType.LIVE
        return None

    async def get_current_playing_picon_url(
        self,
        channel_name: str | None = None,
        currservice_serviceref: str | None = None,
    ) -> str | None:
        """Return the URL to the picon image for the currently playing channel.

        :param channel_name: If specified, it will base url on this channel
        name else, fetch latest from get_status_info()
        :param currservice_serviceref: The service_ref for the current service
        :return: The URL, or None if not available
        """

        if channel_name is None:
            channel_name = self.status.currservice.station

        currservice_serviceref = str(self.status.currservice.serviceref)

        if self.status.is_recording_playback:
            channel_name = self.get_channel_name_from_serviceref()

        url = f"/picon/{self.get_picon_name(str(channel_name))}.png"
        _LOGGER.debug("trying picon url (by channel name): %s", url)
        if await self.url_exists(url):
            return url

        # Last ditch attempt.
        # Now try old way, using service ref name.
        # See https://github.com/home-assistant/home-assistant/issues/22293
        #
        # e.g.
        # sref: "1:0:19:2887:40F:1:C00000:0:0:0:"
        # url: http://vusolo2/picon/1_0_19_2887_40F_1_C00000_0_0_0.png)
        url = f"/picon/{currservice_serviceref.strip(':').replace(':', '_')}.png"
        _LOGGER.debug("trying picon url (with sref): %s", url)
        if await self.url_exists(url):
            return url

        _LOGGER.debug("Could not find picon for: %s", channel_name)
        return None

    def get_channel_name_from_serviceref(self) -> str | None:
        """Try to get the channel name from the recording file name."""
        if self.status.currservice.serviceref is None:
            return None
        if len(splits := self.status.currservice.serviceref.split("-")) >= 2:
            # We guess the channel name based on the default recording name scheme
            # Example file name: "20201019 2027 - SBS6 HD - Chateau Meiland"
            return splits[1].strip()
        return self.status.currservice.serviceref

    async def url_exists(self, url: str) -> bool:
        """Check if a given URL responds to a HEAD request.

        :param url: url to test
        :return: True or False
        """

        if self._session is None:
            self._session = aiohttp.ClientSession(self.base)

        request = await self._session.head(url)
        if request.status == 200:
            return True

        _LOGGER.debug("url at %s does not exist.", url)
        return False

    @staticmethod
    def get_picon_name(channel_name: str) -> str:
        """Get the name as format is outlined here.

        https://github.com/openatv/enigma2/blob/master/lib/python/Components/Renderer/Picon.py

        :param channel_name: The name of the channel
        :return: the correctly formatted name
        """
        _LOGGER.debug("Getting Picon URL for %s", channel_name)

        return sub(
            "[^a-z0-9]",
            "",
            (
                unicodedata.normalize("NFKD", channel_name)
                .encode("ASCII", "ignore")
                .decode("utf-8")
            )
            .replace("&", "and")
            .replace("+", "plus")
            .replace("*", "star")
            .lower(),
        )

    async def get_version(self) -> str:
        """Return the Openwebif version."""

        about = await self.get_about()
        return str(about["info"]["webifver"]) if about is not None else None

    async def get_bouquet_sources(self, bouquet: str | None = None) -> dict[str, Any]:
        """Get a dict of source names and sources in the bouquet.

        If bouquet is None, the first bouquet will be read from.

        :param bouquet: The bouquet
        :return: a dict
        """
        sources: dict[str, Any] = {}
        bRef = ""

        if not self.bouquet_list:
            self.bouquet_list = OrderedDict(
                {b[1]: b[0] for b in (await self.get_all_bouquets())["bouquets"]},
            )

        if not bouquet:
            # load first bouquet
            if len(self.bouquet_list) == 0:
                _LOGGER.debug("%s: No bouquets were found.", self.base)
                return sources
            bRef = next(iter(self.bouquet_list.values()))

        elif not bouquet.startswith("1:7:1:0:0:0:0:0:0:0"):
            # Not a bouquet reference -> get bref from name

            bRef = self.bouquet_list.get(bouquet)
            if not bRef:
                _LOGGER.error('Specified bouquet "%s" could not be found', bouquet)
                return sources

        result = await self._call_api(PATH_EPGNOW, {"bRef": bRef})

        if result and "events" in result and len(result["events"]) > 0:
            sources = {
                src["sname"]: src["sref"]
                for src in result["events"]
                if "sname" in src and "sref" in src
            }
        else:
            _LOGGER.warning("No sources could be loaded from specified bouquet.")
        return sources

    async def get_all_services(self) -> dict[str, Any]:
        """Get list of all services."""
        return await self._call_api(PATH_GETALLSERVICES)

    async def get_bouquets(self, stype: SType = SType.TV) -> dict[str, Any]:
        """Get list of bouquets."""
        return await self._call_api(PATH_BOUQUETS, {"stype": str(stype)})

    async def get_all_bouquets(self) -> dict[str, Any]:
        """Get list of all tv and radio bouquets."""
        return {
            **(await self.get_bouquets(SType.TV)),
            **(await self.get_bouquets(SType.RADIO)),
        }

    async def zap(self, source: str) -> bool:
        """Change channel to selected source.

        :param source: the sRef of the channel.
        """

        return self._check_response_result(
            await self._call_api(PATH_ZAP, {"sRef": source}),
        )

    async def _call_api(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> dict[str, Any]:
        """Perform one api request operation."""
        if self._session is None:
            self._session = aiohttp.ClientSession(self.base)
        async with self._session.get(path, params=params) as response:
            _LOGGER.debug("Got %d from: %s", response.status, response.request_info.url)
            if response.status == 401:
                raise InvalidAuthError
            if response.status != 200:
                _LOGGER.error(
                    "Got %d from %s: %s",
                    response.status,
                    response.request_info.url,
                    await response.text(),
                )
                raise ConnectionError
            return dict(await response.json(content_type=None))
