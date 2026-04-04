# Copyright 2025 New Vector Ltd.
# Copyright 2017 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging

from aiohttp import web

from sydent.db.threepid_associations import GlobalAssociationStore
from sydent.http.servlets import MatrixRestError, get_args, json_response

logger = logging.getLogger(__name__)


async def handle_bulk_lookup_post(request: web.Request) -> web.Response:
    """
    Bulk-lookup for threepids.
    Params: 'threepids': list of threepids, each of which is a list of medium, address
    Returns: Object with key 'threepids', which is a list of results where each result
             is a 3 item list of medium, address, mxid
             Note that results are not streamed to the client.
    Threepids for which no mapping is found are omitted.
    """
    sydent = request.app["sydent"]

    args = await get_args(request, ("threepids",))

    threepids = args["threepids"]
    if not isinstance(threepids, list):
        raise MatrixRestError(400, "M_INVALID_PARAM", "threepids must be a list")

    logger.info("Bulk lookup of %d threepids", len(threepids))

    globalAssocStore = GlobalAssociationStore(sydent)
    results = globalAssocStore.getMxids(threepids)

    return json_response({"threepids": results})
