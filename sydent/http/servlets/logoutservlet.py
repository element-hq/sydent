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

from sydent.db.accounts import AccountStore
from sydent.http.auth import authV2, tokenFromRequest
from sydent.http.servlets import MatrixRestError, SydentResource, jsonwrap, send_cors
from sydent.types import JsonDict

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)


class LogoutServlet(SydentResource):
    isLeaf = True

    def __init__(self, syd: "Sydent") -> None:
        super().__init__()
        self.sydent = syd

    @jsonwrap
    def render_POST(self, request: Request) -> JsonDict:
        """
        Invalidate the given access token
        """
        send_cors(request)

        authV2(self.sydent, request, False)

        token = tokenFromRequest(request)
        if token is None:
            raise MatrixRestError(400, "M_MISSING_PARAMS", "Missing token")

        accountStore = AccountStore(self.sydent)
        accountStore.delToken(token)
        return {}

    def render_OPTIONS(self, request: Request) -> bytes:
        send_cors(request)
        return b""
