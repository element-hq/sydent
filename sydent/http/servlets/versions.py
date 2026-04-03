# Copyright 2025 New Vector Ltd.
# Copyright 2022 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from aiohttp import web

from sydent.http.servlets import json_response


async def handle_versions_get(request: web.Request) -> web.Response:
    """
    Return the supported Matrix versions.
    """
    return json_response(
        {
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
    )
