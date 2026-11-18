# Copyright 2026 Element Creations Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.

from unittest.mock import MagicMock, patch

import signedjson.key
import signedjson.sign
from twisted.internet import defer
from twisted.trial import unittest
from unpaddedbase64 import encode_base64

from sydent.hs_federation.types import VerifyKeys
from sydent.hs_federation.verifier import Verifier

_ORIGIN = "homeserver.example.com"
_DESTINATION_IS = "identity.example.com"
_PATH = "/_matrix/identity/v2/3pid/unbind"
_CONTENT = {
    "address": "user@example.com",
    "medium": "email",
    "mxid": "@user:homeserver.example.com",
}


class AuthenticateRequestTestCase(unittest.TestCase):
    def test_signed_request_with_bytes_method_and_uri_authenticates(self) -> None:
        """A signed request authenticates when Twisted supplies method and uri as bytes."""
        signing_key = signedjson.key.generate_signing_key("1")
        key_id = "%s:%s" % (signing_key.alg, signing_key.version)
        signed = signedjson.sign.sign_json(
            {
                "method": "POST",
                "uri": _PATH,
                "destination_is": _DESTINATION_IS,
                "origin": _ORIGIN,
                "content": _CONTENT,
            },
            _ORIGIN,
            signing_key,
        )
        signature = signed["signatures"][_ORIGIN][key_id]

        sydent = MagicMock()
        sydent.config.general.server_name = _DESTINATION_IS
        verifier = Verifier(sydent)

        request = MagicMock()
        request.method = b"POST"
        request.uri = _PATH.encode("ascii")
        request.requestHeaders.getRawHeaders.return_value = [
            'X-Matrix origin="%s",key="%s",sig="%s"' % (_ORIGIN, key_id, signature)
        ]

        async def mock_get_keys(server_name: str) -> VerifyKeys:
            self.assertEqual(server_name, _ORIGIN)
            verify_key = signedjson.key.get_verify_key(signing_key)
            return {key_id: {"key": encode_base64(verify_key.encode())}}

        with patch.object(verifier, "_getKeysForServer", mock_get_keys):
            origin = self.successResultOf(
                defer.ensureDeferred(verifier.authenticate_request(request, _CONTENT))
            )

        self.assertEqual(origin, _ORIGIN)
