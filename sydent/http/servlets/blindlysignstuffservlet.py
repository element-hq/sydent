# Copyright 2025 New Vector Ltd.
# Copyright 2016 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging

import signedjson.key
import signedjson.sign
from aiohttp import web

from sydent.db.invite_tokens import JoinTokenStore
from sydent.http.auth import authV2
from sydent.http.servlets import MatrixRestError, get_args, json_response

logger = logging.getLogger(__name__)


async def handle_blindly_sign_stuff_post(
    request: web.Request, require_auth: bool = False
) -> web.Response:
    sydent = request.app["sydent"]
    server_name = sydent.config.general.server_name
    tokenStore = JoinTokenStore(sydent)

    if require_auth:
        await authV2(sydent, request)

    args = await get_args(request, ("private_key", "token", "mxid"))

    private_key_base64 = args["private_key"]
    token = args["token"]
    mxid = args["mxid"]

    sender = tokenStore.getSenderForToken(token)
    if sender is None:
        raise MatrixRestError(404, "M_UNRECOGNIZED", "Didn't recognize token")

    to_sign = {
        "mxid": mxid,
        "sender": sender,
        "token": token,
    }
    try:
        private_key = signedjson.key.decode_signing_key_base64(
            "ed25519", "0", private_key_base64
        )
        signed = signedjson.sign.sign_json(to_sign, server_name, private_key)
    except Exception:
        logger.exception("signing failed")
        raise MatrixRestError(500, "M_UNKNOWN", "Internal Server Error")

    return json_response(signed)
