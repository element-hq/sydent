# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.

from typing import List
from unittest.mock import MagicMock, patch

from twisted.internet import defer
from twisted.trial import unittest

from sydent.hs_federation.types import SignedMatrixRequest
from sydent.hs_federation.verifier import Verifier

_ORIGIN = "homeserver.com"
_PATH = "/_matrix/identity/v2/3pid/unbind"


class AuthenticateRequestTestCase(unittest.TestCase):
    """Twisted IRequest.method/uri are bytes; signedjson requires str."""

    def test_method_and_uri_are_decoded_to_str(self) -> None:
        sydent = MagicMock()
        sydent.config.general.server_name = _ORIGIN
        verifier = Verifier(sydent)

        request = MagicMock()
        request.method = b"POST"
        request.uri = _PATH.encode("ascii")
        request.requestHeaders.getRawHeaders.return_value = [
            f'X-Matrix origin="{_ORIGIN}",key="ed25519:1",sig="abc"'
        ]

        captured: List[SignedMatrixRequest] = []

        async def mock_verify(
            signed_json: SignedMatrixRequest,
            acceptable_server_names=None,
        ):
            captured.append(signed_json)
            return (_ORIGIN, "ed25519:1")

        with patch.object(verifier, "verifyServerSignedJson", mock_verify):
            origin = self.successResultOf(
                defer.ensureDeferred(verifier.authenticate_request(request, {}))
            )

        self.assertEqual(origin, _ORIGIN)
        self.assertEqual(len(captured), 1)
        signed = captured[0]
        self.assertIsInstance(signed.method, str)
        self.assertIsInstance(signed.uri, str)
        self.assertEqual(signed.method, "POST")
        self.assertEqual(signed.uri, _PATH)
