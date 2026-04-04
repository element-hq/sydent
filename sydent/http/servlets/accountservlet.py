# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from aiohttp import web

from sydent.http.auth import authV2
from sydent.http.servlets import json_response


async def handle_account_get(request: web.Request) -> web.Response:
    """
    Return information about the user's account
    (essentially just a 'who am i')
    """
    sydent = request.app["sydent"]

    account = await authV2(sydent, request)

    return json_response(
        {
            "user_id": account.userId,
        }
    )
