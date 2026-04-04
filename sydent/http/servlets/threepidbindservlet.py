# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
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
from sydent.http.servlets import MatrixRestError, get_args, json_response
from sydent.util.stringutils import is_valid_client_secret
from sydent.validators import (
    IncorrectClientSecretException,
    InvalidSessionIdException,
    SessionExpiredException,
    SessionNotValidatedException,
)


async def handle_threepid_bind_post(
    request: web.Request, require_auth: bool = False
) -> web.Response:
    sydent = request.app["sydent"]

    account = None
    if require_auth:
        account = await authV2(sydent, request)

    args = await get_args(request, ("sid", "client_secret", "mxid"))

    sid = args["sid"]
    mxid = args["mxid"]
    clientSecret = args["client_secret"]

    if not is_valid_client_secret(clientSecret):
        raise MatrixRestError(400, "M_INVALID_PARAM", "Invalid client_secret provided")

    if account:
        # This is a v2 API so only allow binding to the logged in user id
        if account.userId != mxid:
            raise MatrixRestError(
                403,
                "M_UNAUTHORIZED",
                "This user is prohibited from binding to the mxid",
            )

    try:
        valSessionStore = ThreePidValSessionStore(sydent)
        s = valSessionStore.getValidatedSession(sid, clientSecret)
    except (IncorrectClientSecretException, InvalidSessionIdException):
        raise MatrixRestError(
            404,
            "M_NO_VALID_SESSION",
            "No valid session was found matching that sid and client secret",
        ) from None
    except SessionExpiredException:
        raise MatrixRestError(
            400,
            "M_SESSION_EXPIRED",
            "This validation session has expired: call requestToken again",
        ) from None
    except SessionNotValidatedException:
        raise MatrixRestError(
            400,
            "M_SESSION_NOT_VALIDATED",
            "This validation session has not yet been completed",
        ) from None

    res = sydent.threepidBinder.addBinding(s.medium, s.address, mxid)
    return json_response(res)
