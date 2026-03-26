# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from typing import TYPE_CHECKING

from twisted.web.server import Request

from sydent.db.valsession import ThreePidValSessionStore
from sydent.http.auth import authV2
from sydent.http.servlets import SydentResource, get_args, jsonwrap, send_cors
from sydent.types import JsonDict
from sydent.util.stringutils import is_valid_client_secret
from sydent.validators import (
    IncorrectClientSecretException,
    InvalidSessionIdException,
    SessionExpiredException,
    SessionNotValidatedException,
)

if TYPE_CHECKING:
    from sydent.sydent import Sydent


class GetValidated3pidServlet(SydentResource):
    isLeaf = True

    def __init__(self, syd: "Sydent", require_auth: bool = False) -> None:
        super().__init__()
        self.sydent = syd
        self.require_auth = require_auth

    @jsonwrap
    def render_GET(self, request: Request) -> JsonDict:
        send_cors(request)
        if self.require_auth:
            authV2(self.sydent, request)

        args = get_args(request, ("sid", "client_secret"))

        sid = args["sid"]
        clientSecret = args["client_secret"]

        if not is_valid_client_secret(clientSecret):
            request.setResponseCode(400)
            return {
                "errcode": "M_INVALID_PARAM",
                "error": "Invalid client_secret provided",
            }

        valSessionStore = ThreePidValSessionStore(self.sydent)

        noMatchError = {
            "errcode": "M_NO_VALID_SESSION",
            "error": "No valid session was found matching that sid and client secret",
        }

        try:
            s = valSessionStore.getValidatedSession(sid, clientSecret)
        except (IncorrectClientSecretException, InvalidSessionIdException):
            request.setResponseCode(404)
            return noMatchError
        except SessionExpiredException:
            request.setResponseCode(400)
            return {
                "errcode": "M_SESSION_EXPIRED",
                "error": "This validation session has expired: call requestToken again",
            }
        except SessionNotValidatedException:
            request.setResponseCode(400)
            return {
                "errcode": "M_SESSION_NOT_VALIDATED",
                "error": "This validation session has not yet been completed",
            }

        return {"medium": s.medium, "address": s.address, "validated_at": s.mtime}

    def render_OPTIONS(self, request: Request) -> bytes:
        send_cors(request)
        return b""
