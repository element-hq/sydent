# Copyright 2025 New Vector Ltd.
# Copyright 2020 Dirk Klimpel
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from aiohttp import web

from sydent.http.servlets import get_args, json_response


async def handle_authenticated_unbind_threepid_post(
    request: web.Request,
) -> web.Response:
    """A handler which allows a caller to unbind any 3pid they want from an mxid.

    It is assumed that authentication happens out of band.
    """
    sydent = request.app["sydent"]
    args = await get_args(request, ("medium", "address", "mxid"))

    threepid = {"medium": args["medium"], "address": args["address"]}

    sydent.threepidBinder.removeBinding(
        threepid,
        args["mxid"],
    )
    return json_response({})
