# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""v2 registration, access tokens, logout and terms of service."""

from __future__ import annotations

import os
from typing import Dict

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.fakes import Reply, json_reply
from tests.blackbox.flows import (
    CORS,
    USERINFO,
    V2,
    Homeserver,
    auth,
    cors_headers,
    register,
)
from tests.blackbox.harness import REPO_ROOT, Sydent

TERMS_EN = [
    "https://example.org/somewhere/terms-2.0-en.html",
    "https://example.org/somewhere/privacy-1.2-en.html",
]


@pytest.fixture(scope="module")
def hs(menv: Env) -> Homeserver:
    return Homeserver(menv, "hs.test")


@pytest.fixture(scope="module")
def sydent(menv: Env, hs: Homeserver) -> Sydent:
    terms = os.path.join(REPO_ROOT, "terms.sample.yaml")
    return menv.sydent({"general": {"terms.path": terms}})


def accept_terms(sydent: Sydent, token: str) -> None:
    resp = sydent.request(
        "POST", f"{V2}/terms", json_body={"user_accepts": TERMS_EN}, headers=auth(token)
    )
    assert resp.status == 200, resp
    assert resp.json() == {}


def test_register_returns_token(sydent: Sydent, hs: Homeserver) -> None:
    resp = sydent.request(
        "POST",
        f"{V2}/account/register",
        json_body={"matrix_server_name": hs.server_name, "access_token": "alice"},
    )
    assert resp.status == 200, resp
    body = resp.json()
    assert set(body) == {"access_token", "token"}
    assert body["access_token"] == body["token"]
    assert cors_headers(resp.headers) == CORS


def test_register_request_to_homeserver(sydent: Sydent, hs: Homeserver) -> None:
    before = len(hs.fake.requests_to(USERINFO))
    register(sydent, hs, "a+b/c")
    req = hs.fake.requests_to(USERINFO)[before]
    assert req.method == "GET"
    assert req.path == f"{USERINFO}?access_token=a%2Bb/c"
    assert req.headers["host"] == hs.server_name
    assert req.conn.sni == hs.host


def test_account_whoami(sydent: Sydent, hs: Homeserver) -> None:
    token = register(sydent, hs, "bob")
    accept_terms(sydent, token)
    resp = sydent.request("GET", f"{V2}/account", headers=auth(token))
    assert resp.status == 200
    assert resp.json() == {"user_id": hs.mxid("bob")}
    by_query = sydent.request("GET", f"{V2}/account?access_token={token}")
    assert by_query.json() == {"user_id": hs.mxid("bob")}


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}]
)
def test_account_requires_valid_token(sydent: Sydent, headers: Dict[str, str]) -> None:
    resp = sydent.request("GET", f"{V2}/account", headers=headers)
    assert resp.status == 401
    assert resp.json() == {"errcode": "M_UNAUTHORIZED", "error": "Unauthorized"}


def test_logout_invalidates_token(sydent: Sydent, hs: Homeserver) -> None:
    token = register(sydent, hs, "carol")
    resp = sydent.request("POST", f"{V2}/account/logout", headers=auth(token))
    assert resp.status == 200
    assert resp.json() == {}
    assert sydent.request("GET", f"{V2}/account", headers=auth(token)).status == 401
    again = sydent.request("POST", f"{V2}/account/logout", headers=auth(token))
    assert again.status == 401


def test_get_terms(sydent: Sydent) -> None:
    resp = sydent.request("GET", f"{V2}/terms")
    assert resp.status == 200
    assert resp.json() == {
        "policies": {
            "terms_of_service": {
                "version": "2.0",
                "en": {"name": "Terms of Service", "url": TERMS_EN[0]},
                "fr": {
                    "name": "Conditions d'utilisation",
                    "url": "https://example.org/somewhere/terms-2.0-fr.html",
                },
            },
            "privacy_policy": {
                "version": "1.2",
                "en": {"name": "Privacy Policy", "url": TERMS_EN[1]},
                "fr": {
                    "name": "Politique de confidentialité",
                    "url": "https://example.org/somewhere/privacy-1.2-fr.html",
                },
            },
        }
    }


def test_terms_gate_v2_endpoints(sydent: Sydent, hs: Homeserver) -> None:
    token = register(sydent, hs, "dave")
    resp = sydent.request("GET", f"{V2}/hash_details", headers=auth(token))
    assert resp.status == 403
    assert resp.json() == {"errcode": "M_TERMS_NOT_SIGNED", "error": "Terms not signed"}
    # Accepting only some documents is not enough.
    partial = sydent.request(
        "POST",
        f"{V2}/terms",
        json_body={"user_accepts": TERMS_EN[:1]},
        headers=auth(token),
    )
    assert partial.status == 200
    still = sydent.request("GET", f"{V2}/hash_details", headers=auth(token))
    assert still.status == 403
    accept_terms(sydent, token)
    ok = sydent.request("GET", f"{V2}/hash_details", headers=auth(token))
    assert ok.status == 200
    assert ok.json()["algorithms"] == ["sha256", "none"]


def test_account_requires_terms(sydent: Sydent, hs: Homeserver) -> None:
    token = register(sydent, hs, "erin")
    resp = sydent.request("GET", f"{V2}/account", headers=auth(token))
    assert resp.status == 403
    assert resp.json()["errcode"] == "M_TERMS_NOT_SIGNED"


def test_terms_unknown_url(sydent: Sydent, hs: Homeserver) -> None:
    token = register(sydent, hs, "frank")
    resp = sydent.request(
        "POST",
        f"{V2}/terms",
        json_body={"user_accepts": ["https://example.org/other"]},
        headers=auth(token),
    )
    assert resp.status == 400
    assert resp.json() == {
        "errcode": "M_UNKNOWN",
        "error": "Unrecognised URLs: https://example.org/other",
    }


def test_terms_post_requires_auth(sydent: Sydent) -> None:
    resp = sydent.request("POST", f"{V2}/terms", json_body={"user_accepts": TERMS_EN})
    assert resp.status == 401


def test_register_invalid_server_name(sydent: Sydent) -> None:
    resp = sydent.request(
        "POST",
        f"{V2}/account/register",
        json_body={"matrix_server_name": "example.com#", "access_token": "a"},
    )
    assert resp.status == 400
    assert resp.json()["errcode"] == "M_INVALID_PARAM"


@pytest.mark.parametrize(
    "reply, message",
    [
        (json_reply({}), "The Matrix homeserver did not include 'sub' in its response"),
        (json_reply({"sub": 1}), "The Matrix homeserver returned a malformed reply"),
        (
            json_reply({"sub": "nocolon"}),
            "The Matrix homeserver returned an invalid MXID",
        ),
        (
            json_reply({"sub": "@a:other.test"}),
            "The Matrix homeserver returned a MXID belonging to another homeserver",
        ),
        (Reply(200, b"not json"), "The Matrix homeserver returned invalid JSON"),
    ],
)
def test_register_bad_userinfo(
    menv: Env, sydent: Sydent, reply: Reply, message: str
) -> None:
    other = Homeserver(menv, f"bad{len(menv.net.hosts)}.test")
    other.fake.route("GET", USERINFO, reply)
    resp = sydent.request(
        "POST",
        f"{V2}/account/register",
        json_body={"matrix_server_name": other.server_name, "access_token": "a"},
    )
    assert resp.status == 500, resp
    assert resp.json() == {"errcode": "M_UNKNOWN", "error": message}


def test_register_ignores_userinfo_status(menv: Env, sydent: Sydent) -> None:
    # Existing behaviour: the status of the userinfo response is not checked.
    other = Homeserver(menv, "status.test")
    other.fake.route(
        "GET", USERINFO, json_reply({"sub": other.mxid("x"), "errcode": "M_X"}, 401)
    )
    resp = sydent.request(
        "POST",
        f"{V2}/account/register",
        json_body={"matrix_server_name": other.server_name, "access_token": "a"},
    )
    assert resp.status == 200, resp


@pytest.mark.parametrize("resolvable", [True, False], ids=["refused", "nxdomain"])
def test_register_unreachable_homeserver(
    menv: Env, sydent: Sydent, resolvable: bool
) -> None:
    name = f"down-{resolvable}.test".lower()
    if resolvable:
        # An address with no fake behind it refuses connections.
        menv.net.add_host(name, menv.net.new_address())
    resp = sydent.request(
        "POST",
        f"{V2}/account/register",
        json_body={"matrix_server_name": f"{name}:8448", "access_token": "a"},
    )
    assert resp.status == 500
    assert resp.json()["errcode"] == "M_UNKNOWN"
    assert resp.json()["error"].startswith("Unable to contact the Matrix homeserver")


def test_homeserver_allow_list(env: Env) -> None:
    allowed = Homeserver(env, "allowed.test")
    other = Homeserver(env, "other.test")
    sydent = env.sydent(
        {
            "general": {
                "homeserver_allow_list": allowed.server_name,
                "enable_v1_access": "false",
            }
        }
    )
    assert register(sydent, allowed, "a")
    resp = sydent.request(
        "POST",
        f"{V2}/account/register",
        json_body={"matrix_server_name": other.server_name, "access_token": "a"},
    )
    assert resp.status == 403
    assert resp.json() == {
        "errcode": "M_UNAUTHORIZED",
        "error": "This homeserver is not authorized to access this server.",
    }
    assert other.fake.requests == []
