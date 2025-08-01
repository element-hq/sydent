# Copyright 2025 New Vector Ltd.
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.
from http import HTTPStatus
from json import JSONDecodeError
from unittest.mock import patch

import twisted.internet.error
import twisted.web.client
from parameterized import parameterized
from twisted.trial import unittest

from tests.utils import make_request, make_sydent


class RegisterTestCase(unittest.TestCase):
    """Tests Sydent's register servlet"""

    def setUp(self) -> None:
        # Create a new sydent
        self.sydent = make_sydent()

    def test_sydent_rejects_invalid_hostname(self) -> None:
        """Tests that the /register endpoint rejects an invalid hostname passed as matrix_server_name"""
        self.sydent.run()

        bad_hostname = "example.com#"

        request, channel = make_request(
            self.sydent.reactor,
            self.sydent.clientApiHttpServer.factory,
            "POST",
            "/_matrix/identity/v2/account/register",
            content={"matrix_server_name": bad_hostname, "access_token": "foo"},
        )

        self.assertEqual(channel.code, 400)

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
        self.sydent.run()
        with patch(
            "sydent.http.httpclient.FederationHttpClient.get_json", side_effect=exc
        ):
            request, channel = make_request(
                self.sydent.reactor,
                self.sydent.clientApiHttpServer.factory,
                "POST",
                "/_matrix/identity/v2/account/register",
                content={
                    "matrix_server_name": "matrix.alice.com",
                    "access_token": "back_in_wonderland",
                },
            )
        self.assertEqual(channel.code, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.assertEqual(channel.json_body["errcode"], "M_UNKNOWN")
        # Check that we haven't just returned the generic error message in asyncjsonwrap
        self.assertNotEqual(channel.json_body["error"], "Internal Server Error")
        self.assertIn("contact", channel.json_body["error"])

    def test_federation_does_not_return_json(self) -> None:
        self.sydent.run()
        exc = JSONDecodeError("ruh roh", "C'est n'est pas une objet JSON", 0)
        with patch(
            "sydent.http.httpclient.FederationHttpClient.get_json", side_effect=exc
        ):
            request, channel = make_request(
                self.sydent.reactor,
                self.sydent.clientApiHttpServer.factory,
                "POST",
                "/_matrix/identity/v2/account/register",
                content={
                    "matrix_server_name": "matrix.alice.com",
                    "access_token": "back_in_wonderland",
                },
            )
        self.assertEqual(channel.code, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.assertEqual(channel.json_body["errcode"], "M_UNKNOWN")
        # Check that we haven't just returned the generic error message in asyncjsonwrap
        self.assertNotEqual(channel.json_body["error"], "Internal Server Error")
        self.assertIn("JSON", channel.json_body["error"])


class RegisterAllowListTestCase(unittest.TestCase):
    """
    Test registering works with the `homeserver_allow_list` config option specified
    """

    def test_registering_not_allowed_if_homeserver_not_in_allow_list(self) -> None:
        config = {
            "general": {
                "homeserver_allow_list": "friendly.com, example.com",
                "enable_v1_access": "false",
            }
        }
        # Create a new sydent with a homeserver_allow_list specified
        self.sydent = make_sydent(test_config=config)
        self.sydent.run()

        request, channel = make_request(
            self.sydent.reactor,
            self.sydent.clientApiHttpServer.factory,
            "POST",
            "/_matrix/identity/v2/account/register",
            content={"matrix_server_name": "not.example.com", "access_token": "foo"},
        )
        self.assertEqual(channel.code, 403)
        self.assertEqual(channel.json_body["errcode"], "M_UNAUTHORIZED")
