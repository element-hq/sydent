# Copyright 2025 New Vector Ltd.
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.
import os.path
from unittest.mock import AsyncMock, Mock, patch

import attr
from twisted.trial import unittest

from sydent.types import JsonDict

from tests.utils import make_request, make_sydent


@attr.s(auto_attribs=True)
class FakeHeader:
    """
    A fake header object
    """

    headers: dict

    def getAllRawHeaders(self):
        return self.headers


@attr.s(auto_attribs=True)
class FakeResponse:
    """A fake twisted.web.IResponse object"""

    # HTTP response code
    code: int

    # Fake Header
    headers: FakeHeader


class TestRequestCode(unittest.TestCase):
    def setUp(self) -> None:
        # Create a new sydent
        config = {
            "general": {
                "templates.path": os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "res"
                ),
            },
        }
        self.sydent = make_sydent(test_config=config)

    def _make_request(self, url: str, body: JsonDict | None = None) -> Mock:
        # Patch out the SMS sending so we can investigate the resulting call.
        with patch(
            "sydent.sms.openmarket.OpenMarketSMS.sendTextSMS",
            new_callable=AsyncMock,
        ) as sendTextSMS:
            request, channel = make_request(
                self.sydent.reactor,
                self.sydent.clientApiHttpServer.factory,
                "POST",
                url,
                body,
            )
            self.assertEqual(channel.code, 200)

        return sendTextSMS

    def test_request_code(self) -> None:
        self.sydent.run()

        sendSMS_mock = self._make_request(
            "/_matrix/identity/api/v1/validate/msisdn/requestToken",
            {
                "phone_number": "447700900750",
                "country": "GB",
                "client_secret": "oursecret",
                "send_attempt": 0,
            },
        )
        sendSMS_mock.assert_called_once()

    def test_request_code_via_url_query_params(self) -> None:
        self.sydent.run()
        url = (
            "/_matrix/identity/api/v1/validate/msisdn/requestToken?"
            "phone_number=447700900750"
            "&country=GB"
            "&client_secret=oursecret"
            "&send_attempt=0"
        )
        sendSMS_mock = self._make_request(url)
        sendSMS_mock.assert_called_once()

    @patch("sydent.http.httpclient.HTTPClient.post_json_maybe_get_json")
    def test_bad_api_response_raises_exception(self, post_json: Mock) -> None:
        """Test that an error response from OpenMarket raises an exception
        and that the requester receives an error code."""

        header = FakeHeader({})
        resp = FakeResponse(code=400, headers=header), {}
        post_json.return_value = resp
        self.sydent.run()
        request, channel = make_request(
            self.sydent.reactor,
            self.sydent.clientApiHttpServer.factory,
            "POST",
            "/_matrix/identity/api/v1/validate/msisdn/requestToken",
            {
                "phone_number": "447700900750",
                "country": "GB",
                "client_secret": "oursecret",
                "send_attempt": 0,
            },
        )
        self.assertEqual(channel.code, 500)
