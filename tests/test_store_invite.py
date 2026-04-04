# Copyright 2025 New Vector Ltd.
# Copyright 2021 Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from sydent.users.accounts import Account

from tests.utils import make_sydent


@pytest.fixture
def sydent():
    config = {
        "email": {
            "email.from": "Sydent Validation <noreply@hostname>",
        },
    }
    return make_sydent(test_config=config)


@pytest.fixture
async def client(sydent):
    app = sydent.clientApiHttpServer.app
    async with TestClient(TestServer(app)) as client:
        yield client


SENDER = "@alice:wonderland"


@pytest.mark.parametrize(
    "address",
    [
        "not@an@email@address",
        "Naughty Nigel <perfectly.valid@mail.address>",
    ],
)
async def test_invalid_email_returns_400(address, client):
    with patch("sydent.http.servlets.store_invite_servlet.authV2") as authV2:
        authV2.return_value = Account(SENDER, 0, None)

        resp = await client.post(
            "/_matrix/identity/v2/store-invite",
            json={
                "address": address,
                "medium": "email",
                "room_id": "!myroom:test",
                "sender": SENDER,
            },
        )

    assert resp.status == 400
    body = await resp.json()
    assert body["errcode"] == "M_INVALID_EMAIL"
