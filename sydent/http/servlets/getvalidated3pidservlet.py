# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from aiohttp import web

from sydent.db.valsession import ThreePidValSessionStore
from sydent.http.auth import authV2
from sydent.http.servlets import get_args, json_response
from sydent.util.stringutils import is_valid_client_secret
from sydent.validators import (
    IncorrectClientSecretException,
    InvalidSessionIdException,
    SessionExpiredException,
    SessionNotValidatedException,
)


async def handle_get_validated_3pid_get(
    request: web.Request, require_auth: bool = False
) -> web.Response:
    sydent = request.app["sydent"]

    if require_auth:
        await authV2(sydent, request)

    args = await get_args(request, ("sid", "client_secret"))

    sid = args["sid"]
    clientSecret = args["client_secret"]

    if not is_valid_client_secret(clientSecret):
        return json_response(
            {
                "errcode": "M_INVALID_PARAM",
                "error": "Invalid client_secret provided",
            },
            status=400,
        )

    valSessionStore = ThreePidValSessionStore(sydent)

    noMatchError = {
        "errcode": "M_NO_VALID_SESSION",
        "error": "No valid session was found matching that sid and client secret",
    }

    try:
        s = valSessionStore.getValidatedSession(sid, clientSecret)
    except (IncorrectClientSecretException, InvalidSessionIdException):
        return json_response(noMatchError, status=404)
    except SessionExpiredException:
        return json_response(
            {
                "errcode": "M_SESSION_EXPIRED",
                "error": "This validation session has expired: call requestToken again",
            },
            status=400,
        )
    except SessionNotValidatedException:
        return json_response(
            {
                "errcode": "M_SESSION_NOT_VALIDATED",
                "error": "This validation session has not yet been completed",
            },
            status=400,
        )

    return json_response(
        {"medium": s.medium, "address": s.address, "validated_at": s.mtime}
    )
