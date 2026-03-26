# Copyright 2018-2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from typing import TYPE_CHECKING

from twisted.web.server import Request

from sydent.http.servlets import SydentResource, get_args, jsonwrap, send_cors
from sydent.types import JsonDict

if TYPE_CHECKING:
    from sydent.sydent import Sydent


class AuthenticatedBindThreePidServlet(SydentResource):
    """A servlet which allows a caller to bind any 3pid they want to an mxid

    It is assumed that authentication happens out of band
    """

    def __init__(self, sydent: "Sydent") -> None:
        super().__init__()
        self.sydent = sydent

    @jsonwrap
    def render_POST(self, request: Request) -> JsonDict:
        send_cors(request)
        args = get_args(request, ("medium", "address", "mxid"))

        return self.sydent.threepidBinder.addBinding(
            args["medium"],
            args["address"],
            args["mxid"],
        )

    def render_OPTIONS(self, request: Request) -> bytes:
        send_cors(request)
        return b""
