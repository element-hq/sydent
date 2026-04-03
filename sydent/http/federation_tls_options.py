# Copyright 2019-2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
import ssl

logger = logging.getLogger(__name__)


class ClientTLSOptionsFactory:
    """Factory for TLS options used when making federation connections to
    remote servers.
    """

    def __init__(self, verify_requests: bool) -> None:
        self.verify_requests = verify_requests

    def get_ssl_context(self, host: str) -> ssl.SSLContext | bool:
        """Return an ssl.SSLContext for the given host, or False to skip
        verification.

        When *verify_requests* is False, returns ``False`` which tells aiohttp
        to disable certificate verification entirely.
        """
        if not self.verify_requests:
            return False
        ctx = ssl.create_default_context()
        return ctx
