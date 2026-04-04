# Copyright 2025 New Vector Ltd.
# Copyright 2016 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import json
import logging
from typing import TYPE_CHECKING, Any, cast

import aiohttp

from sydent.http.blacklisting_reactor import BlacklistingResolver
from sydent.http.federation_tls_options import ClientTLSOptionsFactory
from sydent.http.httpcommon import BodyExceededMaxSize, read_body_with_max_size
from sydent.http.matrixfederationagent import MatrixFederationAgent
from sydent.types import JsonDict
from sydent.util import json_decoder

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)


class HTTPClient:
    """A base HTTP client using aiohttp.ClientSession."""

    @property
    def session(self) -> aiohttp.ClientSession:
        raise NotImplementedError

    async def get_json(self, uri: str, max_size: int | None = None) -> JsonDict:
        """Make a GET request to an endpoint returning JSON and parse result.

        :param uri: The URI to make a GET request to.
        :param max_size: The maximum size (in bytes) to allow as a response.

        :return: Parsed JSON as a Python dict.
        """
        logger.debug("HTTP GET %s", uri)

        async with self.session.get(uri) as response:
            body = await response.read()
            if max_size is not None and len(body) > max_size:
                raise BodyExceededMaxSize()
            try:
                json_body = json_decoder.decode(body.decode("UTF-8"))
            except Exception:
                logger.warning("Error parsing JSON from %s", uri)
                raise
            if not isinstance(json_body, dict):
                raise TypeError
            return cast(JsonDict, json_body)

    async def post_json_get_nothing(
        self, uri: str, post_json: JsonDict, opts: dict[str, Any]
    ) -> aiohttp.ClientResponse:
        """Make a POST request to an endpoint returning nothing.

        :param uri: The URI to make a POST request to.
        :param post_json: A Python object that will be converted to a JSON
            string and POSTed to the given URI.
        :param opts: A dictionary of request options. Currently only opts["headers"]
            is supported.

        :return: A response from the remote server.
        """
        resp, _ = await self.post_json_maybe_get_json(uri, post_json, opts)
        return resp

    async def post_json_maybe_get_json(
        self,
        uri: str,
        post_json: dict[str, Any],
        opts: dict[str, Any],
        max_size: int | None = None,
    ) -> tuple[aiohttp.ClientResponse, JsonDict | None]:
        """Make a POST request to an endpoint that might return JSON and parse
        the result.

        :param uri: The URI to make a POST request to.
        :param post_json: A Python object that will be converted to a JSON
            string and POSTed to the given URI.
        :param opts: A dictionary of request options. Currently only opts["headers"]
            is supported.
        :param max_size: The maximum size (in bytes) to allow as a response.

        :return: A tuple of (response, parsed JSON body or None).
        """
        json_bytes = json.dumps(post_json).encode("utf8")

        headers = opts.get(
            "headers",
            {"Content-Type": "application/json"},
        )

        logger.debug("HTTP POST %s -> %s", json_bytes, uri)

        async with self.session.post(uri, data=json_bytes, headers=headers) as response:
            # Read everything inside the context manager so the response
            # remains usable after it exits.
            json_body = None
            try:
                body = await response.read()
                if max_size is not None and len(body) > max_size:
                    raise BodyExceededMaxSize()
                json_body = json_decoder.decode(body.decode("UTF-8"))
            except Exception:
                # We might get an exception because the body exceeds
                # max_size, or it isn't valid JSON. In both cases we don't
                # care.
                pass

            return response, json_body


class SimpleHttpClient(HTTPClient):
    """A simple, no-frills HTTP client based on the class of the same name
    from Synapse.
    """

    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent
        self._session: aiohttp.ClientSession | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            resolver = BlacklistingResolver(
                ip_whitelist=self.sydent.config.general.ip_whitelist,
                ip_blacklist=self.sydent.config.general.ip_blacklist,
            )
            connector = aiohttp.TCPConnector(resolver=resolver)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(connect=15),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


class FederationHttpClient(HTTPClient):
    """HTTP client for federation requests to homeservers. Uses a
    MatrixFederationAgent.
    """

    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent
        self._session: aiohttp.ClientSession | None = None
        self._agent: MatrixFederationAgent | None = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            resolver = BlacklistingResolver(
                ip_whitelist=self.sydent.config.general.ip_whitelist,
                ip_blacklist=self.sydent.config.general.ip_blacklist,
            )
            connector = aiohttp.TCPConnector(resolver=resolver)
            self._session = aiohttp.ClientSession(connector=connector)

            tls_factory = (
                ClientTLSOptionsFactory(self.sydent.config.http.verify_federation_certs)
                if self.sydent.use_tls_for_federation
                else None
            )

            self._agent = MatrixFederationAgent(
                session=self._session,
                tls_client_options_factory=tls_factory,
            )
        return self._session

    @property
    def session(self) -> aiohttp.ClientSession:
        return self._ensure_session()

    @property
    def agent(self) -> MatrixFederationAgent:
        self._ensure_session()
        assert self._agent is not None
        return self._agent

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def get_json(self, uri: str, max_size: int | None = None) -> JsonDict:
        """Make a GET request via the federation agent."""
        logger.debug("HTTP GET %s", uri)

        response = await self.agent.request("GET", uri)
        try:
            body = await read_body_with_max_size(response, max_size)
            json_body = json_decoder.decode(body.decode("UTF-8"))
        except Exception:
            logger.warning("Error parsing JSON from %s", uri)
            raise
        if not isinstance(json_body, dict):
            raise TypeError
        return cast(JsonDict, json_body)

    async def post_json_get_nothing(
        self, uri: str, post_json: JsonDict, opts: dict[str, Any]
    ) -> aiohttp.ClientResponse:
        resp, _ = await self.post_json_maybe_get_json(uri, post_json, opts)
        return resp

    async def post_json_maybe_get_json(
        self,
        uri: str,
        post_json: dict[str, Any],
        opts: dict[str, Any],
        max_size: int | None = None,
    ) -> tuple[aiohttp.ClientResponse, JsonDict | None]:
        json_bytes = json.dumps(post_json).encode("utf8")

        headers = opts.get(
            "headers",
            {"Content-Type": "application/json"},
        )

        logger.debug("HTTP POST %s -> %s", json_bytes, uri)

        response = await self.agent.request(
            "POST",
            uri,
            headers=headers,
            body=json_bytes,
        )

        json_body = None
        try:
            body = await read_body_with_max_size(response, max_size)
            json_body = json_decoder.decode(body.decode("UTF-8"))
        except Exception:
            pass

        return response, json_body
