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

from sydent.http.auth import authV2
from sydent.http.servlets import json_response

logger = logging.getLogger(__name__)

KNOWN_ALGORITHMS = ["sha256", "none"]


async def handle_hash_details_get(
    request: web.Request, lookup_pepper: str
) -> web.Response:
    """
    Return the hashing algorithms and pepper that this IS supports. The
    pepper included in the response is stored in the database, or
    otherwise generated.

    Returns: An object containing an array of hashing algorithms the
             server supports, and a `lookup_pepper` field, which is a
             server-defined value that the client should include in the 3PID
             information before hashing.
    """
    sydent = request.app["sydent"]

    await authV2(sydent, request)

    return json_response(
        {
            "algorithms": KNOWN_ALGORITHMS,
            "lookup_pepper": lookup_pepper,
        }
    )
