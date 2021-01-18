"""Support for the Spokestack speech service."""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import uuid

import aiohttp
import async_timeout
import voluptuous as vol

from homeassistant.components.tts import CONF_LANG, PLATFORM_SCHEMA, Provider
from homeassistant.const import CONF_CLIENT_ID, HTTP_OK
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DEFAULT_LANG,
    DEFAULT_MODE,
    DEFAULT_VOICE,
    GRAPHQL_METHODS,
    MODE,
    SECRET,
    VOICE,
)

_LOGGER = logging.getLogger(__name__)

SPOKESTACK_URL = "https://api.spokestack.io/v1"

SUPPORTED_LANGUAGES = ["en", "en-us"]
SUPPORTED_MODES = ["markdown", "ssml", "text"]


def _uuid(id_str):
    """Convert to UUID for validation, but save as string."""
    return str(uuid.UUID(id_str))


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_CLIENT_ID): _uuid,
        vol.Required(SECRET): str,
        vol.Optional(CONF_LANG, default=DEFAULT_LANG): vol.In(SUPPORTED_LANGUAGES),
        vol.Optional(MODE, default=DEFAULT_MODE): str,
        vol.Optional(VOICE, default=DEFAULT_VOICE): str,
    }
)


def get_engine(hass, config, discovery_info=None):
    """Set up Spokestack TTS provider."""
    return SpokestackProvider(hass, config)


async def async_get_engine(hass, config, discovery_info=None):
    """Set up Spokestack TTS provider."""
    return SpokestackProvider(hass, config)


class SpokestackProvider(Provider):
    """The Spokestack TTS provider."""

    def __init__(self, hass, config):
        """Init Spokestack TTS service."""
        self.hass = hass
        self._lang = config.get(CONF_LANG, DEFAULT_LANG)
        self._client = config[CONF_CLIENT_ID]
        self._secret = config[SECRET].encode("utf-8")
        self.headers = {"Content-Type": "application/json"}
        self.name = "Spokestack TTS"

    @property
    def default_language(self):
        """Return the default language."""
        return DEFAULT_LANG

    @property
    def supported_languages(self):
        """Return list of supported languages."""
        return SUPPORTED_LANGUAGES

    async def async_get_tts_audio(self, message, language, options=None):
        """Synthesize speech and read audio from the resulting URL."""

        options = options or {}
        websession = async_get_clientsession(self.hass)

        body, method = self._request_body(message, options)
        signature = self._sign(body)

        headers = {
            "Authorization": f"Spokestack {self._client}:{signature}",
            **self.headers,
        }

        data = b""

        try:
            with async_timeout.timeout(10):
                response = await websession.post(
                    SPOKESTACK_URL, data=body, headers=headers
                )

                if response.status != HTTP_OK:
                    _LOGGER.error(
                        "Spokestack TTS error: %d (%s)",
                        response.status,
                        await response.text(),
                    )
                    return None, None

                resp_body = await response.json()
                stream_url = self._get_stream_url(resp_body, method)

                if not stream_url:
                    _LOGGER.error("No TTS URL found: %s", str(resp_body))
                    return None, None

                data = await self._read_stream(websession, stream_url)

        except (asyncio.TimeoutError, aiohttp.ClientError):
            _LOGGER.error("Spokestack TTS timed out")
            return None, None

        return "mp3", data

    def _request_body(self, message, options):
        voice = options.get("voice", DEFAULT_VOICE)
        mode = options.get("mode", DEFAULT_MODE)
        method = GRAPHQL_METHODS.get(mode)

        graphql = f"""
        query HASynthesis($voice: String!, ${mode}: String!) {{
          {method}(voice: $voice, {mode}: ${mode}) {{
            url
          }}
        }}
        """

        return (
            json.dumps(
                {
                    "query": graphql,
                    "variables": {
                        "voice": voice,
                        mode: message,
                    },
                }
            ),
            method,
        )

    def _sign(self, body: str) -> str:
        algo = hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256)
        return base64.b64encode(algo.digest()).decode("utf-8")

    @staticmethod
    def _get_stream_url(response, method):
        return response.get("data", {}).get(method, {}).get("url")

    @staticmethod
    async def _read_stream(websession, stream_url: str) -> bytes:
        audio = b""
        try:
            with async_timeout.timeout(10):
                response = await websession.get(stream_url)

                if response.status != HTTP_OK:
                    _LOGGER.error(
                        "Spokestack TTS audio error: %d (%s)",
                        response.status,
                        await response.text(),
                    )
                    return audio

                async for data, _ in response.content.iter_chunks():
                    audio += data

        except (asyncio.TimeoutError, aiohttp.ClientError):
            _LOGGER.error("Spokestack TTS timed out")
            return None

        return audio
