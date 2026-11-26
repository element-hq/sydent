# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Third-party invites: store-invite, the invite email, sign-ed25519 and onbind."""

from __future__ import annotations

import email
import email.header
import re
import urllib.parse
from typing import Any, Dict

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.fakes import wait_until
from tests.blackbox.flows import (
    ONBIND,
    V1,
    V2,
    Homeserver,
    auth,
    internal_bind,
    pubkey,
    register,
    verify_signed,
)
from tests.blackbox.harness import Sydent

SERVER_NAME = "invites.test"


@pytest.fixture(scope="module")
def hs(menv: Env) -> Homeserver:
    return Homeserver(menv, "hs.test")


@pytest.fixture(scope="module")
def sydent(menv: Env, hs: Homeserver) -> Sydent:
    return menv.sydent(
        {
            "email": {
                "email.ratelimit_sender.burst": "1000",
                "email.third_party_invite_keyword_blocklist": "\nforbiddenword",
                "email.third_party_invite_room_blocklist": "\n!blocked:hs.test",
            }
        },
        server_name=SERVER_NAME,
        internal_api=True,
    )


@pytest.fixture(scope="module")
def token(sydent: Sydent, hs: Homeserver) -> str:
    return register(sydent, hs, "inviter")


def store_invite(sydent: Sydent, token: str, sender: str, **extra: Any) -> Any:
    body: Dict[str, Any] = {
        "medium": "email",
        "address": "Invitee@Example.TEST",
        "room_id": "!room:hs.test",
        "sender": sender,
    }
    body.update(extra)
    return sydent.request(
        "POST", f"{V2}/store-invite", json_body=body, headers=auth(token)
    )


def test_store_invite_response_and_mail(
    menv: Env, sydent: Sydent, hs: Homeserver, token: str
) -> None:
    before = len(menv.smtp.messages)
    resp = store_invite(
        sydent,
        token,
        hs.mxid("inviter"),
        room_name="My room",
        sender_display_name="Inviter",
    )
    assert resp.status == 200, resp
    body = resp.json()
    assert set(body) == {"token", "public_key", "public_keys", "display_name"}
    assert re.fullmatch(r"[A-Za-z]{128}", body["token"])
    assert body["public_key"] == pubkey(sydent)
    assert body["display_name"] == "Inv...@Exa..."
    base = f"http://{SERVER_NAME}/_matrix/identity/api/v1"
    assert body["public_keys"][0] == {
        "public_key": body["public_key"],
        "key_validity_url": f"{base}/pubkey/isvalid",
    }
    ephemeral = body["public_keys"][1]
    assert ephemeral["key_validity_url"] == f"{base}/pubkey/ephemeral/isvalid"
    valid = sydent.request(
        "GET",
        f"{V2}/pubkey/ephemeral/isvalid?"
        + urllib.parse.urlencode({"public_key": ephemeral["public_key"]}),
    )
    assert valid.json() == {"valid": True}

    mail = menv.smtp.wait_for(before + 1)[before]
    assert mail.rcpt_to == ["invitee@example.test"]
    message = email.message_from_bytes(mail.data)
    subject = str(
        email.header.make_header(email.header.decode_header(message["Subject"]))
    )
    assert subject == "Inviter has invited you to chat"
    assert body["token"] in mail.text
    assert "My room" in mail.text


def test_sender_must_match_account(sydent: Sydent, token: str) -> None:
    resp = store_invite(sydent, token, "@someone-else:hs.test")
    assert resp.status == 403
    assert resp.json() == {
        "errcode": "M_UNAUTHORIZED",
        "error": "'sender' doesn't match",
    }


def test_unauthenticated_v2_request_is_bad_json(sydent: Sydent) -> None:
    # Existing behaviour: the body is consumed before the access token is looked
    # for in it, so the missing token surfaces as malformed JSON.
    resp = sydent.request(
        "POST",
        f"{V2}/store-invite",
        json_body={
            "medium": "email",
            "address": "a@b.test",
            "room_id": "!r:hs.test",
            "sender": "@a:hs.test",
        },
    )
    assert resp.status == 400
    assert resp.json()["errcode"] == "M_BAD_JSON"


def test_unsupported_medium(sydent: Sydent, hs: Homeserver, token: str) -> None:
    resp = store_invite(sydent, token, hs.mxid("inviter"), medium="msisdn", address="1")
    assert resp.status == 400
    assert resp.json()["errcode"] == "M_UNRECOGNIZED"


def test_already_bound(sydent: Sydent, hs: Homeserver, token: str) -> None:
    internal_bind(sydent, "email", "taken@example.test", "@taken:hs.test")
    resp = store_invite(sydent, token, hs.mxid("inviter"), address="Taken@example.test")
    assert resp.status == 400
    assert resp.json() == {
        "errcode": "M_THREEPID_IN_USE",
        "error": "Binding already known",
        "mxid": "@taken:hs.test",
    }


@pytest.mark.parametrize(
    "extra",
    [{"room_id": "!blocked:hs.test"}, {"room_name": "a ForbiddenWord here"}],
)
def test_blocklists(
    menv: Env, sydent: Sydent, hs: Homeserver, token: str, extra: Dict[str, str]
) -> None:
    before = len(menv.smtp.messages)
    resp = store_invite(sydent, token, hs.mxid("inviter"), **extra)
    assert resp.status == 403
    assert resp.json() == {"errcode": "M_UNAUTHORIZED", "error": "Invite not allowed"}
    assert len(menv.smtp.messages) == before


def test_v1_store_invite_without_auth(menv: Env, sydent: Sydent) -> None:
    resp = sydent.request(
        "POST",
        f"{V1}/store-invite",
        json_body={
            "medium": "email",
            "address": "v1invitee@example.test",
            "room_id": "!room:hs.test",
            "sender": "@anyone:hs.test",
        },
    )
    assert resp.status == 200, resp


def test_invite_delivered_on_bind_and_signable(
    menv: Env, sydent: Sydent, hs: Homeserver, token: str
) -> None:
    address = "bindinvite@example.test"
    resp = store_invite(sydent, token, hs.mxid("inviter"), address=address)
    assert resp.status == 200
    invite_token = resp.json()["token"]

    before = len(hs.fake.requests_to(ONBIND))
    assoc = internal_bind(sydent, "email", address, hs.mxid("invitee"))
    assert assoc["invites"][0]["token"] == invite_token
    onbind = wait_until(lambda: hs.fake.requests_to(ONBIND)[before:])[0]
    invites = onbind.json()["invites"]
    assert len(invites) == 1
    invite = invites[0]
    assert invite["token"] == invite_token
    assert invite["sender"] == hs.mxid("inviter")
    assert invite["mxid"] == hs.mxid("invitee")
    assert invite["room_id"] == "!room:hs.test"
    verify_signed(invite["signed"], SERVER_NAME, pubkey(sydent))
    assert invite["signed"]["mxid"] == hs.mxid("invitee")
    assert invite["signed"]["token"] == invite_token


def test_sign_ed25519(menv: Env, sydent: Sydent, hs: Homeserver, token: str) -> None:
    before = len(menv.smtp.messages)
    resp = store_invite(
        sydent, token, hs.mxid("inviter"), address="signme@example.test"
    )
    assert resp.status == 200
    invite_token = resp.json()["token"]
    mail = menv.smtp.wait_for(before + 1)[before]
    match = re.search(r"private_key%3D([A-Za-z0-9_\-]+)", mail.text)
    assert match, mail.text
    private_key = match.group(1)
    signed = sydent.request(
        "POST",
        f"{V2}/sign-ed25519",
        json_body={
            "private_key": private_key,
            "token": invite_token,
            "mxid": hs.mxid("x"),
        },
        headers=auth(token),
    )
    assert signed.status == 200, signed
    body = signed.json()
    assert {k: body[k] for k in ("mxid", "sender", "token")} == {
        "mxid": hs.mxid("x"),
        "sender": hs.mxid("inviter"),
        "token": invite_token,
    }
    ephemeral = resp.json()["public_keys"][1]["public_key"]
    verify_signed(body, SERVER_NAME, ephemeral)


def test_sign_ed25519_unknown_token(sydent: Sydent, token: str) -> None:
    resp = sydent.request(
        "POST",
        f"{V2}/sign-ed25519",
        json_body={"private_key": "x", "token": "nope", "mxid": "@a:hs.test"},
        headers=auth(token),
    )
    assert resp.status == 404
    assert resp.json() == {
        "errcode": "M_UNRECOGNIZED",
        "error": "Didn't recognize token",
    }
