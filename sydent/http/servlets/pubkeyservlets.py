# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from aiohttp import web
from unpaddedbase64 import encode_base64

from sydent.db.invite_tokens import JoinTokenStore
from sydent.http.servlets import get_args, json_response


async def handle_ed25519_get(request: web.Request) -> web.Response:
    sydent = request.app["sydent"]
    pubKey = sydent.keyring.ed25519.verify_key
    pubKeyBase64 = encode_base64(pubKey.encode())

    return json_response({"public_key": pubKeyBase64})


async def handle_pubkey_is_valid_get(request: web.Request) -> web.Response:
    sydent = request.app["sydent"]
    args = await get_args(request, ("public_key",))

    pubKey = sydent.keyring.ed25519.verify_key
    pubKeyBase64 = encode_base64(pubKey.encode())

    return json_response({"valid": args["public_key"] == pubKeyBase64})


async def handle_ephemeral_pubkey_is_valid_get(request: web.Request) -> web.Response:
    sydent = request.app["sydent"]
    joinTokenStore = JoinTokenStore(sydent)
    args = await get_args(request, ("public_key",))
    publicKey = args["public_key"]

    return json_response(
        {
            "valid": joinTokenStore.validateEphemeralPublicKey(publicKey),
        }
    )
