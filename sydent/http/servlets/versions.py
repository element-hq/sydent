# Copyright 2025 New Vector Ltd.
# Copyright 2022 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from twisted.web.server import Request

from sydent.http.servlets import SydentResource, jsonwrap, send_cors
from sydent.types import JsonDict


class VersionsServlet(SydentResource):
    isLeaf = True

    @jsonwrap
    def render_GET(self, request: Request) -> JsonDict:
        """
        Return the supported Matrix versions.
        """
        send_cors(request)

        return {
            "versions": [
                "r0.1.0",
                "r0.2.0",
                "r0.2.1",
                "r0.3.0",
                "v1.1",
                "v1.2",
                "v1.3",
                "v1.4",
                "v1.5",
            ]
        }

    def render_OPTIONS(self, request: Request) -> bytes:
        send_cors(request)
        return b""
