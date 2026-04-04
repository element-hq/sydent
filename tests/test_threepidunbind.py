# Copyright 2025 New Vector Ltd.
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

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


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError(),
        OSError("DNS lookup failed"),
        TimeoutError(),
    ],
)
async def test_connection_failure(exc, client, sydent):
    """Check we respond sensibly if we can't contact the homeserver."""
    with patch.object(sydent.sig_verifier, "authenticate_request", side_effect=exc):
        resp = await client.post(
            "/_matrix/identity/v2/3pid/unbind",
            json={
                "mxid": "@alice:wonderland",
                "threepid": {
                    "address": "alice.cooper@wonderland.biz",
                    "medium": "email",
                },
            },
        )
    assert resp.status == 500
    body = await resp.json()
    assert body["errcode"] == "M_UNKNOWN"
    assert "contact" in body["error"]
