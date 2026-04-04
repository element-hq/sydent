# Copyright 2025 New Vector Ltd.
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from netaddr import IPAddress, IPSet

from sydent.http.blacklisting_reactor import check_against_blacklist
from sydent.http.srvresolver import Server

from tests.utils import make_sydent


class TestCheckAgainstBlacklist:
    """Tests for the check_against_blacklist pure function."""

    def test_blacklisted_ip_is_blocked(self):
        ip = IPAddress("5.6.7.8")
        blacklist = IPSet(["5.0.0.0/8"])
        assert check_against_blacklist(ip, None, blacklist) is True

    def test_safe_ip_is_not_blocked(self):
        ip = IPAddress("1.2.3.4")
        blacklist = IPSet(["5.0.0.0/8"])
        assert check_against_blacklist(ip, None, blacklist) is False

    def test_whitelisted_ip_overrides_blacklist(self):
        ip = IPAddress("5.1.1.1")
        whitelist = IPSet(["5.1.1.1"])
        blacklist = IPSet(["5.0.0.0/8"])
        assert check_against_blacklist(ip, whitelist, blacklist) is False

    def test_blacklisted_ip_not_in_whitelist_is_blocked(self):
        ip = IPAddress("5.6.7.8")
        whitelist = IPSet(["5.1.1.1"])
        blacklist = IPSet(["5.0.0.0/8"])
        assert check_against_blacklist(ip, whitelist, blacklist) is True

    def test_ip_not_in_blacklist_with_whitelist(self):
        ip = IPAddress("1.2.3.4")
        whitelist = IPSet(["5.1.1.1"])
        blacklist = IPSet(["5.0.0.0/8"])
        assert check_against_blacklist(ip, whitelist, blacklist) is False


@pytest.fixture
def blacklist_sydent():
    config = {
        "general": {
            "ip.blacklist": "5.0.0.0/8",
            "ip.whitelist": "5.1.1.1",
        },
    }
    return make_sydent(test_config=config)


@pytest.fixture
async def blacklist_client(blacklist_sydent):
    app = blacklist_sydent.clientApiHttpServer.app
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_federation_client_allowed_ip(blacklist_client):
    """Test that register succeeds when the federation call is mocked."""
    # Mock the federation call that validates the openid token.
    with patch(
        "sydent.http.servlets.registerservlet.FederationHttpClient",
    ) as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.get_json = AsyncMock(return_value={"sub": "@test:example.com"})

        resp = await blacklist_client.post(
            "/_matrix/identity/v2/account/register",
            json={
                "access_token": "foo",
                "expires_in": 300,
                "matrix_server_name": "example.com",
                "token_type": "Bearer",
            },
        )

    assert resp.status == 200


async def test_federation_client_safe_ip(blacklist_client):
    """Test that register succeeds when the federation call is mocked (safe IP)."""
    with patch(
        "sydent.http.servlets.registerservlet.FederationHttpClient",
    ) as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.get_json = AsyncMock(return_value={"sub": "@test:example.com"})

        resp = await blacklist_client.post(
            "/_matrix/identity/v2/account/register",
            json={
                "access_token": "foo",
                "expires_in": 300,
                "matrix_server_name": "example.com",
                "token_type": "Bearer",
            },
        )

    assert resp.status == 200


@patch("sydent.http.srvresolver.SrvResolver.resolve_service")
async def test_federation_client_unsafe_ip(resolver, blacklist_client):
    """Test that requests to blacklisted IPs are rejected."""
    resolver.return_value = [
        Server(
            host=b"danger.test",
            port=443,
            priority=1,
            weight=1,
            expires=100,
        )
    ]

    resp = await blacklist_client.post(
        "/_matrix/identity/v2/account/register",
        json={
            "access_token": "foo",
            "expires_in": 300,
            "matrix_server_name": "example.com",
            "token_type": "Bearer",
        },
    )

    assert resp.status == 500
