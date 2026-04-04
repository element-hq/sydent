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

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.utils import make_sydent


@pytest.fixture
def sydent():
    config = {
        "general": {
            "templates.path": os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "res"
            ),
        },
    }
    return make_sydent(test_config=config)


@pytest.fixture
async def client(sydent):
    app = sydent.clientApiHttpServer.app
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_request_code(client):
    with patch(
        "sydent.sms.openmarket.OpenMarketSMS.sendTextSMS",
        new_callable=AsyncMock,
    ) as sendTextSMS:
        sendTextSMS.return_value = Mock()

        resp = await client.post(
            "/_matrix/identity/api/v1/validate/msisdn/requestToken",
            json={
                "phone_number": "447700900750",
                "country": "GB",
                "client_secret": "oursecret",
                "send_attempt": 0,
            },
        )
        assert resp.status == 200
        sendTextSMS.assert_called_once()


async def test_request_code_via_url_query_params(client):
    url = (
        "/_matrix/identity/api/v1/validate/msisdn/requestToken?"
        "phone_number=447700900750"
        "&country=GB"
        "&client_secret=oursecret"
        "&send_attempt=0"
    )
    with patch(
        "sydent.sms.openmarket.OpenMarketSMS.sendTextSMS",
        new_callable=AsyncMock,
    ) as sendTextSMS:
        sendTextSMS.return_value = Mock()

        resp = await client.post(url)
        assert resp.status == 200
        sendTextSMS.assert_called_once()


@patch("sydent.http.httpclient.HTTPClient.post_json_maybe_get_json")
async def test_bad_api_response_raises_exception(post_json, client):
    """Test that an error response from OpenMarket raises an exception
    and that the requester receives an error code."""
    post_json.return_value = (Mock(status=400, headers={}), {})

    resp = await client.post(
        "/_matrix/identity/api/v1/validate/msisdn/requestToken",
        json={
            "phone_number": "447700900750",
            "country": "GB",
            "client_secret": "oursecret",
            "send_attempt": 0,
        },
    )
    assert resp.status == 500
