# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging

from aiohttp import web

from sydent.db.accounts import AccountStore
from sydent.http.auth import authV2, tokenFromRequest
from sydent.http.servlets import MatrixRestError, json_response

logger = logging.getLogger(__name__)


async def handle_logout_post(request: web.Request) -> web.Response:
    """
    Invalidate the given access token
    """
    sydent = request.app["sydent"]

    await authV2(sydent, request, False)

    token = await tokenFromRequest(request)
    if token is None:
        raise MatrixRestError(400, "M_MISSING_PARAMS", "Missing token")

    accountStore = AccountStore(sydent)
    accountStore.delToken(token)
    return json_response({})
