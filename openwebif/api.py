"""API for communicating with OpenWebIf."""

import logging
import unicodedata
from dataclasses import dataclass
from re import sub
from time import time
from typing import Any, Mapping, Optional

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
)
from .error import InvalidAuthError

_LOGGER = logging.getLogger(__name__)


def enable_logging():
    """Set up the logging for home assistant."""
    logging.basicConfig(level=logging.INFO)


@dataclass
class OpenWebIfServiceEvent:
    """Represent a OpenWebIf service event."""

    filename: str = None
    id: int = None
    name: str = None
    serviceref: str = None
    begin: str = None
    begin_timestamp: int = None
    end: str = None
    end_timestamp: int = None
    description: str = None
    fulldescription: str = None
    station: str = None


@dataclass
class OpenWebIfStatus:
    """Repesent a OpenWebIf status."""

    volume: int = None
    muted: bool = None
    currservice: OpenWebIfServiceEvent = None
    in_standby: bool = False
    is_recording: bool = False
    streaming_list: str = None
    is_streaming: bool = False
    status_info: dict = None
    is_recording_playback: bool = False


class OpenWebIfDevice:
    """Represent a OpenWebIf client device."""

    _session: aiohttp.ClientSession
    _base: URL
    status: OpenWebIfStatus = OpenWebIfStatus()
    is_offline: bool = False
    turn_off_to_deep: bool
    picon_url: str = None
    source_bouquet: str = None
    mac_address: str = None

    # pylint: disable=too-many-arguments, disable=too-many-instance-attributes
    def __init__(
        self,
        host: str,
        port: int = 80,
        username: str = None,
        password: str = None,
        is_https: bool = False,
        turn_off_to_deep: bool = False,
        source_bouquet: str = None,
    ):
        """Define an enigma2 device.

        :param host: IP or hostname
        :param port: OpenWebif port
        :param username: e2 user
        :param password: e2 user password
        :param is_https: use https or not
        :param turn_off_to_deep: If True, send to deep standby on turn off
        :param source_bouquet: Which bouquet ref you want to load
        """
        enable_logging()

        _LOGGER.debug("Initialising new openwebif client for host: %s", host)
        _LOGGER.debug("%s Using a single session client.", host)

        self._base = URL.build(
            scheme="http" if not is_https else "https",
            host=host,
            port=port,
            user=username,
            password=password,
        )

        self._session = aiohttp.ClientSession(self._base)
        self.turn_off_to_deep = turn_off_to_deep
        self.source_bouquet = source_bouquet
        self.status.currservice = OpenWebIfServiceEvent()

        self.sources = None
        self.source_list = None

    async def close(self):
        """Close the connection."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    def default_all(self):
        """Set all properties to default."""
        self.status = OpenWebIfStatus()
        self.status.currservice = OpenWebIfServiceEvent()

    async def get_about(self) -> dict:
        """Get general information."""
        return await self._call_api(PATH_ABOUT)

    async def update(self):
        """Refresh current state based from <host>/api/statusinfo."""
        self.status.status_info = await self._call_api(PATH_STATUSINFO)

        if self.is_offline or not self.status.status_info:
            self.default_all()
            return

        self.status.currservice.filename = self.status.status_info["currservice_filename"]
        self.status.currservice.id = self.status.status_info["currservice_id"]
        self.status.currservice.name = self.status.status_info["currservice_name"]
        if "currservice_serviceref" in self.status.status_info:
            self.status.currservice.serviceref = self.status.status_info["currservice_serviceref"]
        self.status.currservice.begin = self.status.status_info["currservice_begin"]
        if "currservice_begin_timestamp" in self.status.status_info:
            self.status.currservice.begin_timestamp = self.status.status_info["currservice_begin_timestamp"]
        self.status.currservice.end = self.status.status_info["currservice_end"]
        if "currservice_end_timestamp" in self.status.status_info:
            self.status.currservice.end_timestamp = self.status.status_info["currservice_end_timestamp"]
        self.status.currservice.description = self.status.status_info["currservice_description"]
        if "currservice_station" in self.status.status_info:
            self.status.currservice.station = self.status.status_info["currservice_station"]
        self.status.currservice.fulldescription = self.status.status_info["currservice_fulldescription"]
        self.status.in_standby = self.status.status_info["inStandby"] == "true"
        self.status.is_recording = self.status.status_info["isRecording"] == "true"
        self.status.is_streaming = self.status.status_info["isStreaming"] == "true"
        self.status.muted = self.status.status_info["muted"]
        self.status.volume = self.status.status_info["volume"]

        if not self.sources:
            self.sources = await self.get_bouquet_sources(bouquet=self.source_bouquet)
            self.source_list = list(self.sources.keys())

        if self.get_current_playback_type() == PlaybackType.recording:
            # try get correct channel name
            channel_name = self.get_channel_name_from_serviceref()
            self.status.status_info["currservice_station"] = channel_name
            self.status.currservice.station = channel_name
            self.status.currservice.name = f"🔴 {self.status.currservice.name}"

        if not self.status.in_standby:
            self.picon_url = str(self._base.with_path(await self.get_current_playing_picon_url(
                channel_name=self.status.currservice.station,
                currservice_serviceref=self.status.currservice.serviceref,
            )))

    async def get_volume(self) -> int:
        """Get the current volume."""
        return (await self._call_api(PATH_VOL))["current"]

    async def set_volume(self, new_volume: int) -> bool:
        """Set the volume to the new value.

        :param new_volume: int from 0-100
        :return: True if successful, false if there was a problem
        """
        return self._check_reponse_result(
            await self._call_api(PATH_VOL, {"set": "set" + str(new_volume)})
        )

    async def send_message(
        self, text: str, message_type: MessageType = MessageType.INFO, timeout: int = -1
    ):
        """Send a message to the TV screen.

        :param text: The message to display
        :param message_type: The type of message (0 = YES/NO, 1 = INFO, 2 = WARNING, 3 = ERROR)
        :return: True if successful, false if there was a problem
        """

        return self._check_reponse_result(
            self._call_api(
                PATH_MESSAGE,
                {"timeout": timeout, "type": message_type.value, "text": text},
            )
        )

    async def turn_on(self):
        """Take the box out of standby."""

        if self.is_offline:
            _LOGGER.debug("Box is offline, going to try wake on lan")
            self.wake_up()

        return self._check_reponse_result(
            await self._call_api(PATH_POWERSTATE, {"newstate": PowerState.WAKEUP})
        )

    def get_screen_grab_url(self, mode: ScreenGrabMode = ScreenGrabMode.ALL, format: ScreenGrabFormat = ScreenGrabFormat.JPG, r: int = 0) -> URL:
        """Get the URL for a screen grab.

        :param mode: The screen grab mode
        :param format: The picture format
        :param r: The resolution to grab (0 = native resolution)
        :return: The URL for the screen grab
        """
        return self._base.with_path(PATH_GRAB).with_query({"mode": mode.value, "format": format.value, "t": int(time()), "r": r})

    async def turn_off(self):
        """Put the box out into standby."""
        if self.turn_off_to_deep:
            return self.deep_standby()

        return self._check_reponse_result(
            await self._call_api(PATH_POWERSTATE, {"newstate": PowerState.STANDBY})
        )

    async def deep_standby(self):
        """Go into deep standby."""

        return self._check_reponse_result(
            await self._call_api(PATH_POWERSTATE, {"newstate": PowerState.DEEP_STANDBY})
        )

    async def send_remote_control_action(self, action: RemoteControlCodes):
        """Send a remote control command."""

        return self._check_reponse_result(
            await self._call_api(PATH_REMOTECONTROL, {"command": action.value})
        )

    async def toggle_mute(self) -> bool:
        """Send mute command."""
        return (await self._call_api(PATH_VOL, {"set": "mute"}))["isMute"]

    @staticmethod
    def _check_reponse_result(response: dict) -> bool:
        """Check the result of the response.

        :param response:
        :return: Returns True if command success, else, False
        """
        return response["result"]

    def is_currently_recording_playback(self) -> bool:
        """Return true if playing back recording."""
        return self.get_current_playback_type() == PlaybackType.recording

    def get_current_playback_type(self) -> PlaybackType:
        """Get the currservice_serviceref playing media type.

        :return: PlaybackType.live or PlaybackType.recording
        """

        if self.status.currservice.serviceref:
            if self.status.currservice.serviceref.startswith("1:0:0"):
                # This is a recording, not a live channel
                return PlaybackType.recording

            return PlaybackType.live
        return None

    async def get_current_playing_picon_url(
        self, channel_name=None, currservice_serviceref=None
    ):
        """Return the URL to the picon image for the currently playing channel.

        :param channel_name: If specified, it will base url on this channel
        name else, fetch latest from get_status_info()
        :param currservice_serviceref: The service_ref for the current service
        :return: The URL, or None if not available
        """
        cached_info = None
        if channel_name is None:
            cached_info = self.status_info
            if "currservice_station" in cached_info:
                channel_name = cached_info["currservice_station"]
            else:
                _LOGGER.debug("No channel currently playing")
                return None

        if currservice_serviceref is None:
            if cached_info is None:
                cached_info = self.status.status_info
            currservice_serviceref = self.status.currservice.serviceref

        if self.status.is_recording_playback:
            channel_name = self.get_channel_name_from_serviceref()

        url = f"/picon/{self.get_picon_name(channel_name)}.png"
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

        # stop here. Some boxes freeze when attempting screen grabs so often.
        # See https://github.com/fbradyirl/openwebifpy/issues/14
        return None

    def get_channel_name_from_serviceref(self) -> str:
        """Try to get the channel name from the recording file name."""
        try:
            return self.status.currservice.serviceref.split("-")[1].strip()
        # pylint: disable=broad-except
        except Exception:
            _LOGGER.debug("cannot determine channel name from recording")
        return self.status.currservice.serviceref

    async def url_exists(self, url):
        """Check if a given URL responds to a HEAD request.

        :param url: url to test
        :return: True or False
        """

        request = await self._session.head(url)
        if request.status == 200:
            return True

        _LOGGER.debug("url at %s does not exist.", url)
        return False

    @staticmethod
    def get_picon_name(channel_name):
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

    async def get_version(self):
        """Return the Openwebif version."""

        return (await self.get_about())["info"]["webifver"]

    async def get_bouquet_sources(self, bouquet: str = None) -> dict:
        """Get a dict of source names and sources in the bouquet.

        If bouquet is None, the first bouquet will be read from.

        :param bouquet: The bouquet
        :return: a dict
        """
        sources = {}

        if not bouquet:
            # load first bouquet
            all_bouquets = await self.get_all_bouquets()
            if not all_bouquets:
                _LOGGER.debug("%s get_all_bouquets: No bouquets were found.", self._base)
                return sources

            if "bouquets" in all_bouquets:
                bouquet = all_bouquets["bouquets"][0][0]
                first_bouquet_name = all_bouquets["bouquets"][0][1]
                _LOGGER.debug(
                    "%s First bouquet name is: '%s'", self._base, first_bouquet_name
                )
            else:
                _LOGGER.debug("bouquets not in all_bouquets.")
                return sources

        result = await self._call_api(PATH_EPGNOW, {"bRef": bouquet})

        if result:
            sources = {src["sname"]: src["sref"] for src in result["events"]}
        else:
            _LOGGER.warning("No sources could be loaded from specified bouquet.")
        return sources

    async def get_all_services(self) -> dict:
        """Get list of all services."""
        return await self._call_api(PATH_GETALLSERVICES)

    async def get_all_bouquets(self):
        """Get list of all bouquets."""
        return await self._call_api(PATH_BOUQUETS)

    async def zap(self, source: str) -> bool:
        """Change channel to selected source.

        :param source: the sRef of the channel.
        """

        return self._check_reponse_result(
            await self._call_api(PATH_ZAP, {"sRef": source})
        )

    async def _call_api(
        self, path: str, params: Optional[Mapping[str, str]] = None
    ) -> Any:
        """Perform one api request operation."""
        async with self._session.get(path, params=params) as response:
            _LOGGER.debug("Got %d from: %s", response.status, response.request_info.url)
            if response.status == 401:
                raise InvalidAuthError
            elif response.status != 200:
                _LOGGER.error(
                    "Got %d from %s: %s",
                    response.status,
                    response.request_info.url,
                    await response.text(),
                )
                if not self.is_offline:
                    _LOGGER.warning("%s is unreachable.", response.request_info.url)
                    self.is_offline = True
                    return None
            return await response.json(content_type=None)
