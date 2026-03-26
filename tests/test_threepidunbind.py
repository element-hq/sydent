# Copyright 2025 New Vector Ltd.
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.
from http import HTTPStatus
from unittest.mock import patch

import twisted.internet.error
import twisted.web.client
from parameterized import parameterized
from twisted.trial import unittest

from tests.utils import make_request, make_sydent


class ThreepidUnbindTestCase(unittest.TestCase):
    """Tests Sydent's threepidunbind servlet"""

    def setUp(self) -> None:
        # Create a new sydent
        self.sydent = make_sydent()

    # Duplicated from TestRegisterServelet. Is there a way for us to keep
    # ourselves DRY?
    @parameterized.expand(
        [
            (twisted.internet.error.DNSLookupError(),),
            (twisted.internet.error.TimeoutError(),),
            (twisted.internet.error.ConnectionRefusedError(),),
            # Naughty: strictly we're supposed to initialise a ResponseNeverReceived
            # with a list of 1 or more failures.
            (twisted.web.client.ResponseNeverReceived([]),),
        ]
    )
    def test_connection_failure(self, exc: Exception) -> None:
        """Check we respond sensibly if we can't contact the homeserver."""
        self.sydent.run()
        with patch.object(
            self.sydent.sig_verifier, "authenticate_request", side_effect=exc
        ):
            request, channel = make_request(
                self.sydent.reactor,
                self.sydent.clientApiHttpServer.factory,
                "POST",
                "/_matrix/identity/v2/3pid/unbind",
                content={
                    "mxid": "@alice:wonderland",
                    "threepid": {
                        "address": "alice.cooper@wonderland.biz",
                        "medium": "email",
                    },
                },
            )
        self.assertEqual(channel.code, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.assertEqual(channel.json_body["errcode"], "M_UNKNOWN")
        self.assertIn("contact", channel.json_body["error"])
