# Copyright 2025 New Vector Ltd.
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from json import JSONDecodeError
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.utils import make_sydent


@pytest.fixture
def sydent():
    return make_sydent()


@pytest.fixture
async def client(sydent):
    app = sydent.clientApiHttpServer.app
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_sydent_rejects_invalid_hostname(client):
    """Tests that the /register endpoint rejects an invalid hostname passed as matrix_server_name."""
    bad_hostname = "example.com#"

    resp = await client.post(
        "/_matrix/identity/v2/account/register",
        json={"matrix_server_name": bad_hostname, "access_token": "foo"},
    )

    assert resp.status == 400


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError(),
        OSError("DNS lookup failed"),
        TimeoutError(),
    ],
)
async def test_connection_failure(exc, client):
    with patch("sydent.http.httpclient.FederationHttpClient.get_json", side_effect=exc):
        resp = await client.post(
            "/_matrix/identity/v2/account/register",
            json={
                "matrix_server_name": "matrix.alice.com",
                "access_token": "back_in_wonderland",
            },
        )
    assert resp.status == 500
    body = await resp.json()
    assert body["errcode"] == "M_UNKNOWN"
    # Check that we haven't just returned the generic error message
    assert body["error"] != "Internal Server Error"
    assert "contact" in body["error"]


async def test_federation_does_not_return_json(client):
    exc = JSONDecodeError("ruh roh", "C'est n'est pas une objet JSON", 0)
    with patch("sydent.http.httpclient.FederationHttpClient.get_json", side_effect=exc):
        resp = await client.post(
            "/_matrix/identity/v2/account/register",
            json={
                "matrix_server_name": "matrix.alice.com",
                "access_token": "back_in_wonderland",
            },
        )
    assert resp.status == 500
    body = await resp.json()
    assert body["errcode"] == "M_UNKNOWN"
    # Check that we haven't just returned the generic error message
    assert body["error"] != "Internal Server Error"
    assert "JSON" in body["error"]


async def test_registering_not_allowed_if_homeserver_not_in_allow_list():
    """Test registering works with the homeserver_allow_list config option specified."""
    config = {
        "general": {
            "homeserver_allow_list": "friendly.com, example.com",
            "enable_v1_access": "false",
        }
    }
    sydent = make_sydent(test_config=config)

    app = sydent.clientApiHttpServer.app
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/_matrix/identity/v2/account/register",
            json={"matrix_server_name": "not.example.com", "access_token": "foo"},
        )
        assert resp.status == 403
        body = await resp.json()
        assert body["errcode"] == "M_UNAUTHORIZED"
