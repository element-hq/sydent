# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
# Copyright 2014-2017 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging

import signedjson.sign
from aiohttp import web

from sydent.db.threepid_associations import GlobalAssociationStore
from sydent.http.servlets import get_args, json_response
from sydent.util import json_decoder

logger = logging.getLogger(__name__)


async def handle_lookup_get(request: web.Request) -> web.Response:
    """
    Look up an individual threepid.

    ** DEPRECATED **

    Params: 'medium': the medium of the threepid
            'address': the address of the threepid
    Returns: A signed association if the threepid has a corresponding mxid, otherwise the empty object.
    """
    sydent = request.app["sydent"]

    args = await get_args(request, ("medium", "address"))

    medium = args["medium"]
    address = args["address"]

    globalAssocStore = GlobalAssociationStore(sydent)

    sgassoc_raw = globalAssocStore.signedAssociationStringForThreepid(medium, address)

    if not sgassoc_raw:
        return json_response({})

    sgassoc = json_decoder.decode(sgassoc_raw)
    if sydent.config.general.server_name not in sgassoc["signatures"]:
        sgassoc = signedjson.sign.sign_json(
            sgassoc,
            sydent.config.general.server_name,
            sydent.keyring.ed25519,
        )
    return json_response(sgassoc)
