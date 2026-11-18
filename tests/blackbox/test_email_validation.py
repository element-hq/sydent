# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Email validation sessions: requestToken, submitToken (POST and GET)."""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.flows import (
    CORS,
    V1,
    V2,
    Homeserver,
    auth,
    cors_headers,
    link_from_mail,
    register,
    request_email_token,
    token_from_mail,
)
from tests.blackbox.harness import Sydent, free_port

SERVER_NAME = "email.test"
SUBMIT_V1 = f"{V1}/validate/email/submitToken"


@pytest.fixture(scope="module")
def sydent(menv: Env) -> Sydent:
    return menv.sydent(
        {"email": {"email.ratelimit_sender.burst": "1000"}}, server_name=SERVER_NAME
    )


def submit(sydent: Sydent, body: Dict[str, Any]) -> Any:
    return sydent.request("POST", SUBMIT_V1, json_body=body)


def test_request_token_sends_mail(menv: Env, sydent: Sydent) -> None:
    sid, mail = request_email_token(sydent, menv, "Someone@Example.TEST", "secret1")
    assert sid.isdigit()
    assert mail.rcpt_to == ["Someone@Example.TEST"]
    assert "To: Someone@Example.TEST" in mail.text
    assert "Subject: Confirm your email address for Matrix" in mail.text
    token = token_from_mail(mail)
    link = urllib.parse.urlsplit(link_from_mail(mail))
    assert f"{link.scheme}://{link.netloc}" == f"http://{SERVER_NAME}"
    assert link.path == SUBMIT_V1
    assert urllib.parse.parse_qs(link.query) == {
        "token": [token],
        "client_secret": ["secret1"],
        "sid": [sid],
    }


def test_request_token_next_link(menv: Env, sydent: Sydent) -> None:
    sid, mail = request_email_token(
        sydent,
        menv,
        "next@example.test",
        "secret2",
        extra={"next_link": "https://client.test/done?x=1"},
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(link_from_mail(mail)).query)
    assert query["nextLink"] == [f"https://client.test/done?x=1&sid={sid}"]


def test_request_token_file_next_link_dropped(menv: Env, sydent: Sydent) -> None:
    _, mail = request_email_token(
        sydent, menv, "file@example.test", "secret3", extra={"next_link": "file:///x"}
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(link_from_mail(mail)).query)
    assert "nextLink" not in query


def test_repeated_send_attempt_sends_mail_again(menv: Env, sydent: Sydent) -> None:
    sid, _ = request_email_token(sydent, menv, "again@example.test", "secret4")
    # Existing behaviour: the stored send attempt is written to the wrong row, so
    # repeating a send_attempt sends the mail again. request_email_token fails
    # if no new mail arrives.
    sid_same, _ = request_email_token(sydent, menv, "again@example.test", "secret4")
    sid_next, _ = request_email_token(
        sydent, menv, "again@example.test", "secret4", send_attempt=2
    )
    assert sid_same == sid_next == sid


@pytest.mark.parametrize(
    "body, errcode",
    [
        (
            {"email": "a@b.test", "client_secret": "s", "send_attempt": "x"},
            "M_INVALID_PARAM",
        ),
        (
            {"email": "a@b.test", "client_secret": "bad secret", "send_attempt": 1},
            "M_INVALID_PARAM",
        ),
        (
            {"email": "a" * 500 + "@b.test", "client_secret": "s", "send_attempt": 1},
            "M_INVALID_PARAM",
        ),
        (
            {"email": "not an email", "client_secret": "s", "send_attempt": 1},
            "M_INVALID_EMAIL",
        ),
        ({"email": "a@b.test"}, "M_MISSING_PARAMS"),
    ],
)
def test_request_token_errors(
    sydent: Sydent, body: Dict[str, Any], errcode: str
) -> None:
    resp = sydent.request("POST", f"{V1}/validate/email/requestToken", json_body=body)
    assert resp.status == 400, resp
    assert resp.json()["errcode"] == errcode


def test_v2_endpoints_require_auth(sydent: Sydent) -> None:
    body = {"email": "a@b.test", "client_secret": "s", "send_attempt": 1}
    req = sydent.request("POST", f"{V2}/validate/email/requestToken", json_body=body)
    assert req.status == 401
    sub = sydent.request(
        "POST",
        f"{V2}/validate/email/submitToken",
        json_body={"sid": "1", "client_secret": "s", "token": "t"},
    )
    assert sub.status == 401


def test_submit_token_post(menv: Env, sydent: Sydent) -> None:
    sid, mail = request_email_token(sydent, menv, "post@example.test", "secret5")
    token = token_from_mail(mail)
    resp = submit(sydent, {"sid": sid, "client_secret": "secret5", "token": token})
    assert resp.status == 200
    assert resp.json() == {"success": True}
    assert cors_headers(resp.headers) == CORS


@pytest.mark.parametrize(
    "case, status, expected",
    [
        # Existing behaviour: these failures are reported with a 200 status, and
        # the messages for a wrong token and an unknown sid are swapped.
        (
            "wrong_token",
            200,
            {
                "success": False,
                "errcode": "M_NO_VALID_SESSION",
                "error": "No session could be found with this sid",
            },
        ),
        (
            "unknown_sid",
            200,
            {
                "success": False,
                "errcode": "M_INVALID_PARAM",
                "error": "The token doesn't match",
            },
        ),
        (
            "wrong_secret",
            200,
            {
                "success": False,
                "errcode": "M_INVALID_PARAM",
                "error": "Client secret does not match the one given when requesting the token",
            },
        ),
        (
            "invalid_secret",
            400,
            {"errcode": "M_INVALID_PARAM", "error": "Invalid client_secret provided"},
        ),
    ],
)
def test_submit_token_post_failures(
    menv: Env, sydent: Sydent, case: str, status: int, expected: Dict[str, Any]
) -> None:
    secret = f"secret-{case.replace('_', '-')}"
    sid, mail = request_email_token(sydent, menv, f"{case}@example.test", secret)
    body = {"sid": sid, "client_secret": secret, "token": token_from_mail(mail)}
    if case == "wrong_token":
        body["token"] = "nope"
    elif case == "unknown_sid":
        body["sid"] = "999999"
    elif case == "wrong_secret":
        body["client_secret"] = "othersecret"
    else:
        body["client_secret"] = "bad secret"
    resp = submit(sydent, body)
    assert resp.status == status, resp
    assert resp.json() == expected


def test_submit_token_get_via_link(menv: Env, sydent: Sydent) -> None:
    _, mail = request_email_token(sydent, menv, "get@example.test", "secret6")
    link = urllib.parse.urlsplit(link_from_mail(mail))
    resp = sydent.request("GET", f"{link.path}?{link.query}")
    assert resp.status == 200
    assert resp.header("Content-Type") == "text/html"
    assert b"Verification successful" in resp.body
    assert cors_headers(resp.headers) == {}


def test_submit_token_get_redirects_to_next_link(menv: Env, sydent: Sydent) -> None:
    sid, mail = request_email_token(
        sydent,
        menv,
        "redirect@example.test",
        "secret7",
        extra={"next_link": "https://client.test/done"},
    )
    link = urllib.parse.urlsplit(link_from_mail(mail))
    resp = sydent.request("GET", f"{link.path}?{link.query}")
    assert resp.status == 302
    assert resp.header("Location") == f"https://client.test/done?sid={sid}"


def test_submit_token_get_ignores_file_next_link(menv: Env, sydent: Sydent) -> None:
    sid, mail = request_email_token(sydent, menv, "getfile@example.test", "secret8")
    query = urllib.parse.urlencode(
        {
            "sid": sid,
            "client_secret": "secret8",
            "token": token_from_mail(mail),
            "nextLink": "file:///etc/passwd",
        }
    )
    resp = sydent.request("GET", f"{SUBMIT_V1}?{query}")
    assert resp.status == 200
    assert resp.header("Location") is None
    assert b"Verification successful" in resp.body


def test_submit_token_get_failure(menv: Env, sydent: Sydent) -> None:
    sid, _ = request_email_token(sydent, menv, "getfail@example.test", "secret9")
    query = urllib.parse.urlencode(
        {
            "sid": sid,
            "client_secret": "secret9",
            "token": "wrong",
            "nextLink": "https://x.test",
        }
    )
    resp = sydent.request("GET", f"{SUBMIT_V1}?{query}")
    # Existing behaviour: a failed validation is reported with a 200 status.
    assert resp.status == 200
    assert resp.header("Location") is None
    assert b"Verification failed" in resp.body


def test_submit_token_get_invalid_secret(sydent: Sydent) -> None:
    resp = sydent.request("GET", f"{SUBMIT_V1}?sid=1&client_secret=a%20b&token=1")
    assert resp.status == 400
    assert resp.header("Content-Type") == "text/html"
    assert b"Verification failed" in resp.body


def test_submit_token_get_missing_params(sydent: Sydent) -> None:
    resp = sydent.request("GET", f"{SUBMIT_V1}?sid=1")
    assert resp.status == 400
    assert b"Verification failed" in resp.body


def test_smtp_failure(env: Env) -> None:
    sydent = env.sydent({"email": {"email.smtpport": str(free_port())}})
    resp = sydent.request(
        "POST",
        f"{V1}/validate/email/requestToken",
        json_body={"email": "a@b.test", "client_secret": "s", "send_attempt": 1},
    )
    assert resp.status == 500
    assert resp.json() == {
        "errcode": "M_EMAIL_SEND_ERROR",
        "error": "Failed to send email",
    }


def test_sender_ratelimit_v1_by_ip(env: Env) -> None:
    sydent = env.sydent({"email": {"email.ratelimit_sender.burst": "2"}})
    statuses = []
    for i in range(3):
        resp = sydent.request(
            "POST",
            f"{V1}/validate/email/requestToken",
            json_body={
                "email": f"r{i}@b.test",
                "client_secret": "s",
                "send_attempt": 1,
            },
        )
        statuses.append(resp.status)
    assert statuses == [200, 200, 429]
    assert resp.json() == {"errcode": "M_UNKNOWN", "error": "Too many requests"}


def test_sender_ratelimit_v2_by_user(env: Env) -> None:
    hs = Homeserver(env, "hs.test")
    sydent = env.sydent({"email": {"email.ratelimit_sender.burst": "1"}})
    alice = register(sydent, hs, "alice")
    bob = register(sydent, hs, "bob")

    def request(token: str, address: str) -> int:
        return sydent.request(
            "POST",
            f"{V2}/validate/email/requestToken",
            json_body={"email": address, "client_secret": "s", "send_attempt": 1},
            headers=auth(token),
        ).status

    assert request(alice, "a1@b.test") == 200
    assert request(alice, "a2@b.test") == 429
    assert request(bob, "b1@b.test") == 200
