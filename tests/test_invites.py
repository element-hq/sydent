from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from sydent.db.invite_tokens import JoinTokenStore
from sydent.http.servlets.store_invite_servlet import redact_email_address

from tests.utils import make_sydent


@pytest.fixture
def sydent():
    config = {
        "email": {
            # Used by test_invited_email_address_obfuscation
            "email.third_party_invite_username_obfuscate_characters": "6",
            "email.third_party_invite_domain_obfuscate_characters": "8",
            "email.third_party_invite_keyword_blocklist": "evil\nbad\nhttps://",
        },
    }
    return make_sydent(test_config=config)


@pytest.fixture
async def client(sydent):
    app = sydent.clientApiHttpServer.app
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_delete_on_bind(sydent, client):
    """Tests that 3PID invite tokens are deleted upon delivery after a successful bind."""
    import asyncio

    medium = "email"
    address = "john@example.com"

    # Mock the federation HTTP call so _notify succeeds without a real network request.
    mock_response = Mock()
    mock_response.status = 200

    with patch(
        "sydent.threepid.bind.FederationHttpClient",
    ) as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.post_json_get_nothing = AsyncMock(return_value=mock_response)

        join_token_store = JoinTokenStore(sydent)
        join_token_store.storeToken(
            medium,
            address,
            "!someroom:example.com",
            "@jane:example.com",
            "sometoken",
        )

        tokens = join_token_store.getTokens(medium, address)
        assert len(tokens) == 1

        # addBinding is synchronous, but creates an asyncio task for _notify
        sydent.threepidBinder.addBinding(
            medium,
            address,
            "@john:example.com",
        )

        # Let the event loop run the _notify task
        await asyncio.sleep(0.1)

    cur = sydent.db.cursor()

    # Manually retrieve the tokens for this 3PID.
    res = cur.execute(
        "SELECT medium, address, room_id, sender, token FROM invite_tokens"
        " WHERE medium = ? AND address = ?",
        (medium, address),
    )
    rows = res.fetchall()

    # Check that we didn't get any result.
    assert len(rows) == 0


def test_invited_email_address_obfuscation(sydent):
    """Test that email addresses included in third-party invites are properly
    obfuscated according to the relevant config options.
    """
    email_address = "1234567890@1234567890.com"
    result = redact_email_address(sydent, email_address)

    assert result == "123456...@12345678..."

    # Even short addresses are redacted
    short_email_address = "1@1.com"
    result = redact_email_address(sydent, short_email_address)

    assert result == "...@1..."


async def test_third_party_invite_keyword_block_works(client):
    invite_config = {
        "medium": "email",
        "address": "foo@example.com",
        "room_id": "!bar",
        "sender": "@foo:example.com",
        "room_name": "This is an EVIL room name.",
    }
    resp = await client.post(
        "/_matrix/identity/api/v1/store-invite",
        json=invite_config,
    )
    assert resp.status == 403


async def test_third_party_invite_keyword_blocklist_exempts_web_client_location_url(
    client,
):
    invite_config = {
        "medium": "email",
        "address": "foo@example.com",
        "room_id": "!bar",
        "sender": "@foo:example.com",
        "room_name": "This is a fine room name.",
        "org.matrix.web_client_location": "https://example.com",
    }

    # don't actually send the email
    with patch("sydent.util.emailutils.smtplib") as smtplib:
        resp = await client.post(
            "/_matrix/identity/api/v1/store-invite",
            json=invite_config,
        )
    assert resp.status == 200
    smtp = smtplib.SMTP.return_value
    # but make sure we did try to send it
    smtp.sendmail.assert_called_once()


@pytest.fixture
def no_delete_sydent():
    config = {"general": {"delete_tokens_on_bind": "false"}}
    return make_sydent(test_config=config)


@pytest.fixture
async def no_delete_client(no_delete_sydent):
    app = no_delete_sydent.clientApiHttpServer.app
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_no_delete_on_bind(no_delete_sydent, no_delete_client):
    """Test that invite tokens are not deleted when that is disabled."""
    import asyncio

    sydent = no_delete_sydent

    medium = "email"
    address = "john@example.com"

    mock_response = Mock()
    mock_response.status = 200

    with patch(
        "sydent.threepid.bind.FederationHttpClient",
    ) as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.post_json_get_nothing = AsyncMock(return_value=mock_response)

        join_token_store = JoinTokenStore(sydent)
        join_token_store.storeToken(
            medium,
            address,
            "!someroom:example.com",
            "@jane:example.com",
            "sometoken",
        )

        tokens = join_token_store.getTokens(medium, address)
        assert len(tokens) == 1

        sydent.threepidBinder.addBinding(
            medium,
            address,
            "@john:example.com",
        )

        await asyncio.sleep(0.1)

    cur = sydent.db.cursor()

    res = cur.execute(
        "SELECT medium, address, room_id, sender, token FROM invite_tokens"
        " WHERE medium = ? AND address = ?",
        (medium, address),
    )
    rows = res.fetchall()

    # Token should still exist since delete_tokens_on_bind is false.
    assert len(rows) == 1
