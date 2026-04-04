# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
import ssl
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from sydent.sydent import Sydent


logger = logging.getLogger(__name__)

# Arbitrarily limited to 512 KiB.
MAX_REQUEST_SIZE = 512 * 1024


class SslComponents:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent
        self.ssl_context = self._make_ssl_context()

    def _make_ssl_context(self) -> ssl.SSLContext | None:
        """Build an SSL context from the configured certificate and CA files.

        Returns None if no certificate file is configured.
        """
        cert_file = self.sydent.config.http.cert_file

        if cert_file == "":
            logger.warning(
                "No HTTPS private key / cert found: not starting replication server "
                "or doing replication pushes"
            )
            return None

        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert_file)
        except OSError:
            logger.warning(
                "Unable to read private key / cert file from %s: not starting the "
                "replication HTTPS server or doing replication pushes.",
                cert_file,
            )
            return None

        ca_cert_file = self.sydent.config.http.ca_cert_file
        if len(ca_cert_file) > 0:
            try:
                ctx.load_verify_locations(ca_cert_file)
            except Exception:
                logger.warning("Failed to open CA cert file %s", ca_cert_file)
                raise
            logger.warning("Using custom CA cert file: %s", ca_cert_file)
            ctx.verify_mode = ssl.CERT_REQUIRED

        return ctx


class BodyExceededMaxSize(Exception):
    """The maximum allowed size of the HTTP body was exceeded."""


async def read_body_with_max_size(
    response: aiohttp.ClientResponse, max_size: int | None
) -> bytes:
    """Read the body of an aiohttp response, enforcing a maximum size.

    Args:
        response: The aiohttp response to read from.
        max_size: The maximum body size in bytes, or None for unlimited.

    Returns:
        The response body as bytes.

    Raises:
        BodyExceededMaxSize: if the body exceeds *max_size*.
    """
    body = await response.read()
    if max_size is not None and len(body) > max_size:
        raise BodyExceededMaxSize()
    return body
