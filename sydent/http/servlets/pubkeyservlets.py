# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from typing import TYPE_CHECKING

from twisted.web.server import Request
from unpaddedbase64 import encode_base64

from sydent.db.invite_tokens import JoinTokenStore
from sydent.http.servlets import SydentResource, get_args, jsonwrap
from sydent.types import JsonDict

if TYPE_CHECKING:
    from sydent.sydent import Sydent


class Ed25519Servlet(SydentResource):
    isLeaf = True

    def __init__(self, syd: "Sydent") -> None:
        super().__init__()
        self.sydent = syd

    @jsonwrap
    def render_GET(self, request: Request) -> JsonDict:
        pubKey = self.sydent.keyring.ed25519.verify_key
        pubKeyBase64 = encode_base64(pubKey.encode())

        return {"public_key": pubKeyBase64}


class PubkeyIsValidServlet(SydentResource):
    isLeaf = True

    def __init__(self, syd: "Sydent") -> None:
        super().__init__()
        self.sydent = syd

    @jsonwrap
    def render_GET(self, request: Request) -> JsonDict:
        args = get_args(request, ("public_key",))

        pubKey = self.sydent.keyring.ed25519.verify_key
        pubKeyBase64 = encode_base64(pubKey.encode())

        return {"valid": args["public_key"] == pubKeyBase64}


class EphemeralPubkeyIsValidServlet(SydentResource):
    isLeaf = True

    def __init__(self, syd: "Sydent") -> None:
        super().__init__()
        self.joinTokenStore = JoinTokenStore(syd)

    @jsonwrap
    def render_GET(self, request: Request) -> JsonDict:
        args = get_args(request, ("public_key",))
        publicKey = args["public_key"]

        return {
            "valid": self.joinTokenStore.validateEphemeralPublicKey(publicKey),
        }
