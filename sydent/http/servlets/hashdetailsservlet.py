# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
from typing import TYPE_CHECKING

from twisted.web.server import Request

from sydent.http.auth import authV2
from sydent.http.servlets import SydentResource, jsonwrap, send_cors
from sydent.types import JsonDict

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)


class HashDetailsServlet(SydentResource):
    isLeaf = True
    known_algorithms = ["sha256", "none"]

    def __init__(self, syd: "Sydent", lookup_pepper: str) -> None:
        super().__init__()
        self.sydent = syd
        self.lookup_pepper = lookup_pepper

    @jsonwrap
    def render_GET(self, request: Request) -> JsonDict:
        """
        Return the hashing algorithms and pepper that this IS supports. The
        pepper included in the response is stored in the database, or
        otherwise generated.

        Returns: An object containing an array of hashing algorithms the
                 server supports, and a `lookup_pepper` field, which is a
                 server-defined value that the client should include in the 3PID
                 information before hashing.
        """
        send_cors(request)

        authV2(self.sydent, request)

        return {
            "algorithms": self.known_algorithms,
            "lookup_pepper": self.lookup_pepper,
        }

    def render_OPTIONS(self, request: Request) -> bytes:
        send_cors(request)
        return b""
