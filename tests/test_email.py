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


async def test_request_code(client):
    with patch("sydent.util.emailutils.smtplib") as smtplib:
        resp = await client.post(
            "/_matrix/identity/api/v1/validate/email/requestToken",
            json={
                "email": "test@test",
                "client_secret": "oursecret",
                "send_attempt": 0,
            },
        )

    assert resp.status == 200

    # Ensure the email is as expected.
    smtp = smtplib.SMTP.return_value
    smtp.sendmail.assert_called_once()
    email_contents = smtp.sendmail.call_args[0][2].decode("utf-8")
    assert "Confirm your email address for Matrix" in email_contents


async def test_request_code_via_url_query_params(client):
    url = (
        "/_matrix/identity/api/v1/validate/email/requestToken?"
        "email=test@test"
        "&client_secret=oursecret"
        "&send_attempt=0"
    )
    with patch("sydent.util.emailutils.smtplib") as smtplib:
        resp = await client.post(url)

    assert resp.status == 200

    # Ensure the email is as expected.
    smtp = smtplib.SMTP.return_value
    smtp.sendmail.assert_called_once()
    email_contents = smtp.sendmail.call_args[0][2].decode("utf-8")
    assert "Confirm your email address for Matrix" in email_contents


async def test_branded_request_code(client):
    with patch("sydent.util.emailutils.smtplib") as smtplib:
        resp = await client.post(
            "/_matrix/identity/api/v1/validate/email/requestToken?brand=vector-im",
            json={
                "email": "test@test",
                "client_secret": "oursecret",
                "send_attempt": 0,
            },
        )

    assert resp.status == 200

    # Ensure the email is as expected.
    smtp = smtplib.SMTP.return_value
    smtp.sendmail.assert_called_once()
    email_contents = smtp.sendmail.call_args[0][2].decode("utf-8")
    assert "Confirm your email address for Element" in email_contents
