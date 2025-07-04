# Copyright 2025 New Vector Ltd.
# Copyright 2020 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from twisted.trial import unittest

from sydent.http.auth import tokenFromRequest
from tests.utils import make_request, make_sydent


class AuthTestCase(unittest.TestCase):
    """Tests Sydent's auth code"""

    def setUp(self):
        # Create a new sydent
        self.sydent = make_sydent()
        self.test_token = "testingtoken"

        # Inject a fake OpenID token into the database
        cur = self.sydent.db.cursor()
        cur.execute(
            "INSERT INTO accounts (user_id, created_ts, consent_version)"
            "VALUES (?, ?, ?)",
            ("@bob:localhost", 101010101, "asd"),
        )
        cur.execute(
            "INSERT INTO tokens (user_id, token)" "VALUES (?, ?)",
            ("@bob:localhost", self.test_token),
        )

        self.sydent.db.commit()

    def test_can_read_token_from_headers(self):
        """Tests that Sydent correctly extracts an auth token from request headers"""
        self.sydent.run()

        request, _ = make_request(
            self.sydent.reactor,
            self.sydent.clientApiHttpServer.factory,
            "GET",
            "/_matrix/identity/v2/hash_details",
        )
        request.requestHeaders.addRawHeader(
            b"Authorization", b"Bearer " + self.test_token.encode("ascii")
        )

        token = tokenFromRequest(request)

        self.assertEqual(token, self.test_token)

    def test_can_read_token_from_query_parameters(self):
        """Tests that Sydent correctly extracts an auth token from query parameters"""
        self.sydent.run()

        request, _ = make_request(
            self.sydent.reactor,
            self.sydent.clientApiHttpServer.factory,
            "GET",
            "/_matrix/identity/v2/hash_details?access_token=" + self.test_token,
        )

        token = tokenFromRequest(request)

        self.assertEqual(token, self.test_token)
