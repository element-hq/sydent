# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import json
import logging
import ssl
from typing import TYPE_CHECKING

import aiohttp

from sydent.types import JsonDict

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)


class ReplicationHttpsClient:
    """
    An HTTPS client specifically for talking replication to other Matrix
    Identity Servers (i.e. presents our replication SSL certificate and
    validates peer SSL certificates as we would in the replication HTTPS
    server).
    """

    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Create the aiohttp session. Must be called within a running event loop."""
        ssl_ctx = self._make_ssl_context()
        if ssl_ctx is not None:
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self.session = aiohttp.ClientSession(connector=connector)

    async def stop(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def postJson(
        self, uri: str, jsonObject: JsonDict
    ) -> aiohttp.ClientResponse | None:
        """
        Sends a POST request over HTTPS.

        :param uri: The URI to send the request to.
        :param jsonObject: The request's body.

        :return: The response, or None if HTTPS is not configured.
        """
        logger.debug("POSTing request to %s", uri)
        if not self.session:
            logger.error("HTTPS post attempted but HTTPS is not configured")
            return None

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Sydent",
        }

        json_bytes = json.dumps(jsonObject).encode("utf8")
        resp = await self.session.post(uri, data=json_bytes, headers=headers)
        return resp

    def _make_ssl_context(self) -> ssl.SSLContext | None:
        """Build a client SSL context for mutual TLS replication."""
        if self.sydent.sslComponents.ssl_context is None:
            return None

        cert_file = self.sydent.config.http.cert_file
        if not cert_file:
            return None

        ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        try:
            ctx.load_cert_chain(cert_file)
        except OSError:
            logger.warning(
                "Unable to load client cert chain from %s for replication",
                cert_file,
            )
            return None

        ca_cert = self.sydent.config.http.ca_cert_file
        if ca_cert:
            try:
                ctx.load_verify_locations(ca_cert)
            except Exception:
                logger.warning("Failed to load CA cert file %s", ca_cert)
                raise

        return ctx
