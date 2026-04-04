# Copyright 2025 New Vector Ltd.
# Copyright 2020 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.utils import make_sydent


@pytest.fixture
def sydent_with_token():
    """Create a Sydent instance with a fake token in the database."""
    sydent = make_sydent()
    test_token = "testingtoken"

    # Inject a fake OpenID token into the database
    cur = sydent.db.cursor()
    cur.execute(
        "INSERT INTO accounts (user_id, created_ts, consent_version)VALUES (?, ?, ?)",
        ("@bob:localhost", 101010101, "asd"),
    )
    cur.execute(
        "INSERT INTO tokens (user_id, token) VALUES (?, ?)",
        ("@bob:localhost", test_token),
    )
    sydent.db.commit()

    return sydent, test_token


@pytest.fixture
async def client(sydent_with_token):
    sydent, _ = sydent_with_token
    app = sydent.clientApiHttpServer.app
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_can_read_token_from_headers(client, sydent_with_token):
    """Tests that Sydent correctly extracts an auth token from request headers"""
    _, test_token = sydent_with_token

    resp = await client.get(
        "/_matrix/identity/v2/hash_details",
        headers={"Authorization": f"Bearer {test_token}"},
    )
    assert resp.status == 200


async def test_can_read_token_from_query_parameters(client, sydent_with_token):
    """Tests that Sydent correctly extracts an auth token from query parameters"""
    _, test_token = sydent_with_token

    resp = await client.get(
        f"/_matrix/identity/v2/hash_details?access_token={test_token}",
    )
    assert resp.status == 200
