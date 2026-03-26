# Copyright 2025 New Vector Ltd.
# Copyright 2018 Travis Ralston
# Copyright 2018 New Vector Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from typing import TYPE_CHECKING

from twisted.web.server import Request

from sydent.http.servlets import SydentResource, jsonwrap, send_cors
from sydent.types import JsonDict

if TYPE_CHECKING:
    from sydent.sydent import Sydent


class CorsServlet(SydentResource):
    isLeaf = False

    def __init__(self, syd: "Sydent") -> None:
        super().__init__()
        self.sydent = syd

    @jsonwrap
    def render_GET(self, request: Request) -> JsonDict:
        send_cors(request)
        return {}

    def render_OPTIONS(self, request: Request) -> bytes:
        send_cors(request)
        return b""
