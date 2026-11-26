# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Binding, unbinding and looking up associations; the internal API listener."""

from __future__ import annotations

import base64
import hashlib
import time
from typing import Any, Dict, List

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.fakes import Reply, Request, http_request, json_reply, wait_until
from tests.blackbox.flows import (
    CORS,
    KEYS,
    ONBIND,
    V1,
    V2,
    Homeserver,
    auth,
    cors_headers,
    internal_bind,
    pubkey,
    register,
    request_email_token,
    v1_lookup,
    validated_email_session,
    verify_signed,
)
from tests.blackbox.harness import Sydent

SERVER_NAME = "lookup.test"
UNBIND = f"{V2}/3pid/unbind"


@pytest.fixture(scope="module")
def hs(menv: Env) -> Homeserver:
    return Homeserver(menv, "hs.test")


@pytest.fixture(scope="module")
def sydent(menv: Env, hs: Homeserver) -> Sydent:
    return menv.sydent(
        {
            # Low enough for test_lookup_v2_errors to exceed it.
            "general": {"address_lookup_limit": "3"},
            "email": {"email.ratelimit_sender.burst": "1000"},
        },
        server_name=SERVER_NAME,
        internal_api=True,
    )


@pytest.fixture(scope="module")
def token(sydent: Sydent, hs: Homeserver) -> str:
    return register(sydent, hs, "alice")


def hash_details(sydent: Sydent, token: str) -> Dict[str, Any]:
    resp = sydent.request("GET", f"{V2}/hash_details", headers=auth(token))
    assert resp.status == 200, resp
    details: Dict[str, Any] = resp.json()
    return details


def lookup_v2(sydent: Sydent, token: str, body: Dict[str, Any]) -> Any:
    return sydent.request("POST", f"{V2}/lookup", json_body=body, headers=auth(token))


def sha256(text: str) -> str:
    digest = hashlib.sha256(text.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def test_internal_bind_then_lookups(sydent: Sydent, token: str) -> None:
    assoc = internal_bind(sydent, "email", "Bound@Example.TEST", "@bound:hs.test")
    assert assoc["medium"] == "email"
    assert assoc["address"] == "bound@example.test"
    assert assoc["mxid"] == "@bound:hs.test"
    assert {"ts", "not_before", "not_after", "signatures"} <= set(assoc)
    verify_signed(assoc, SERVER_NAME, pubkey(sydent))

    v1 = v1_lookup(sydent, "email", "bound@example.test")
    assert v1["mxid"] == "@bound:hs.test"
    verify_signed(v1, SERVER_NAME, pubkey(sydent))

    pepper = hash_details(sydent, token)["lookup_pepper"]
    plain = lookup_v2(
        sydent,
        token,
        {
            "addresses": ["bound@example.test email", "nobody@example.test email"],
            "algorithm": "none",
            "pepper": pepper,
        },
    )
    assert plain.status == 200
    assert plain.json() == {"mappings": {"bound@example.test email": "@bound:hs.test"}}

    hashed = sha256(f"bound@example.test email {pepper}")
    resp = lookup_v2(
        sydent,
        token,
        {"addresses": [hashed, sha256("x")], "algorithm": "sha256", "pepper": pepper},
    )
    assert resp.status == 200
    assert resp.json() == {"mappings": {hashed: "@bound:hs.test"}}

    bulk = sydent.request(
        "POST",
        f"{V1}/bulk_lookup",
        json_body={
            "threepids": [["email", "bound@example.test"], ["email", "x@y.test"]]
        },
    )
    assert bulk.status == 200
    assert bulk.json() == {
        "threepids": [["email", "bound@example.test", "@bound:hs.test"]]
    }


def test_hash_details(sydent: Sydent, token: str) -> None:
    details = hash_details(sydent, token)
    assert details["algorithms"] == ["sha256", "none"]
    assert isinstance(details["lookup_pepper"], str) and details["lookup_pepper"]
    assert sydent.request("GET", f"{V2}/hash_details").status == 401


@pytest.mark.parametrize(
    "body, errcode",
    [
        ({"addresses": "x", "algorithm": "none"}, "M_INVALID_PARAM"),
        ({"addresses": [], "algorithm": "md5"}, "M_INVALID_PARAM"),
        ({"addresses": ["a", "b", "c", "d"], "algorithm": "none"}, "M_TOO_LARGE"),
        ({"addresses": [], "algorithm": "none", "pepper": "wrong"}, "M_INVALID_PEPPER"),
        ({"addresses": ["no-medium"], "algorithm": "none"}, "M_UNKNOWN"),
        ({"algorithm": "none"}, "M_MISSING_PARAMS"),
    ],
)
def test_lookup_v2_errors(
    sydent: Sydent, token: str, body: Dict[str, Any], errcode: str
) -> None:
    pepper = hash_details(sydent, token)["lookup_pepper"]
    body = {"pepper": pepper, **body}
    resp = lookup_v2(sydent, token, body)
    assert resp.status == 400, resp
    assert resp.json()["errcode"] == errcode
    if errcode == "M_INVALID_PEPPER":
        assert resp.json()["lookup_pepper"] == pepper
        assert resp.json()["algorithm"] == "none"


def test_lookup_v2_requires_auth(sydent: Sydent) -> None:
    resp = sydent.request("POST", f"{V2}/lookup", json_body={})
    assert resp.status == 401


def test_internal_api_is_a_separate_listener(sydent: Sydent) -> None:
    on_client = sydent.request(
        "POST",
        "/_matrix/identity/internal/bind",
        json_body={"medium": "email", "address": "x@y.test", "mxid": "@x:hs.test"},
    )
    assert on_client.status == 404
    on_internal = http_request(sydent.internal_url, "GET", f"{V2}")
    assert on_internal.status == 404


def test_internal_api_sends_cors(sydent: Sydent) -> None:
    resp = http_request(
        sydent.internal_url,
        "POST",
        "/_matrix/identity/internal/bind",
        json_body={
            "medium": "email",
            "address": "cors@example.test",
            "mxid": "@c:hs.test",
        },
    )
    assert resp.status == 200
    assert cors_headers(resp.headers) == CORS


def test_internal_api_accepts_large_bodies(sydent: Sydent) -> None:
    body = {
        "medium": "email",
        "address": "large@example.test",
        "mxid": "@l:hs.test",
        "padding": "x" * (2 * 1024 * 1024),
    }
    resp = http_request(
        sydent.internal_url, "POST", "/_matrix/identity/internal/bind", json_body=body
    )
    assert resp.status == 200, resp


def test_internal_unbind(sydent: Sydent) -> None:
    internal_bind(sydent, "email", "gone@example.test", "@gone:hs.test")
    resp = http_request(
        sydent.internal_url,
        "POST",
        "/_matrix/identity/internal/unbind",
        json_body={
            "medium": "email",
            "address": "gone@example.test",
            "mxid": "@gone:hs.test",
        },
    )
    assert resp.status == 200, resp
    assert v1_lookup(sydent, "email", "gone@example.test") == {}


def test_bind_v2_with_validated_session(
    menv: Env, sydent: Sydent, hs: Homeserver, token: str
) -> None:
    secret = "secret-bind-v2"
    sid = validated_email_session(sydent, menv, "v2bind@example.test", secret)
    validated = sydent.request(
        "GET",
        f"{V2}/3pid/getValidated3pid?sid={sid}&client_secret={secret}",
        headers=auth(token),
    )
    assert validated.status == 200
    assert validated.json()["medium"] == "email"
    assert validated.json()["address"] == "v2bind@example.test"
    assert isinstance(validated.json()["validated_at"], int)

    before = len(hs.fake.requests_to(ONBIND))
    resp = sydent.request(
        "POST",
        f"{V2}/3pid/bind",
        json_body={"sid": sid, "client_secret": secret, "mxid": hs.mxid("alice")},
        headers=auth(token),
    )
    assert resp.status == 200, resp
    assoc = resp.json()
    assert assoc["mxid"] == hs.mxid("alice")
    verify_signed(assoc, SERVER_NAME, pubkey(sydent))

    onbind = wait_until(lambda: hs.fake.requests_to(ONBIND)[before:])[0]
    assert onbind.method == "POST"
    assert onbind.headers["host"] == hs.server_name
    assert onbind.headers["content-type"] == "application/json"
    assert onbind.conn.sni == hs.host
    assert onbind.json() == assoc

    assert v1_lookup(sydent, "email", "v2bind@example.test")["mxid"] == hs.mxid("alice")


def test_bind_v2_other_mxid_forbidden(
    menv: Env, sydent: Sydent, hs: Homeserver, token: str
) -> None:
    secret = "secret-bind-other"
    sid = validated_email_session(sydent, menv, "other@example.test", secret)
    resp = sydent.request(
        "POST",
        f"{V2}/3pid/bind",
        json_body={"sid": sid, "client_secret": secret, "mxid": hs.mxid("mallory")},
        headers=auth(token),
    )
    assert resp.status == 403
    assert resp.json()["errcode"] == "M_UNAUTHORIZED"


def test_bind_v1_without_auth(menv: Env, sydent: Sydent, hs: Homeserver) -> None:
    secret = "secret-bind-v1"
    sid = validated_email_session(sydent, menv, "v1bind@example.test", secret)
    resp = sydent.request(
        "POST",
        f"{V1}/3pid/bind",
        json_body={"sid": sid, "client_secret": secret, "mxid": hs.mxid("v1user")},
    )
    assert resp.status == 200, resp
    assert resp.json()["mxid"] == hs.mxid("v1user")


def test_bind_unvalidated_session(menv: Env, sydent: Sydent, token: str) -> None:
    secret = "secret-unvalidated"
    sid, _ = request_email_token(sydent, menv, "unvalidated@example.test", secret)
    validated = sydent.request(
        "GET",
        f"{V2}/3pid/getValidated3pid?sid={sid}&client_secret={secret}",
        headers=auth(token),
    )
    assert validated.status == 400
    assert validated.json()["errcode"] == "M_SESSION_NOT_VALIDATED"
    resp = sydent.request(
        "POST",
        f"{V1}/3pid/bind",
        json_body={"sid": sid, "client_secret": secret, "mxid": "@a:hs.test"},
    )
    assert resp.status == 400
    assert resp.json()["errcode"] == "M_SESSION_NOT_VALIDATED"


@pytest.mark.parametrize(
    "sid, secret, status, errcode",
    [
        ("999999", "wrongsecret", 404, "M_NO_VALID_SESSION"),
        ("1", "bad secret!", 400, "M_INVALID_PARAM"),
    ],
)
def test_get_validated_3pid_errors(
    sydent: Sydent, token: str, sid: str, secret: str, status: int, errcode: str
) -> None:
    resp = sydent.request(
        "GET",
        f"{V2}/3pid/getValidated3pid?sid={sid}&client_secret={secret.replace(' ', '%20')}",
        headers=auth(token),
    )
    assert resp.status == status
    assert resp.json()["errcode"] == errcode


def test_bind_errors(sydent: Sydent) -> None:
    missing = sydent.request("POST", f"{V1}/3pid/bind", json_body={"sid": "1"})
    assert missing.status == 400
    assert missing.json()["errcode"] == "M_MISSING_PARAMS"
    unknown = sydent.request(
        "POST",
        f"{V1}/3pid/bind",
        json_body={"sid": "999999", "client_secret": "abc", "mxid": "@a:hs.test"},
    )
    assert unknown.status == 404
    assert unknown.json()["errcode"] == "M_NO_VALID_SESSION"
    v2 = sydent.request(
        "POST",
        f"{V2}/3pid/bind",
        json_body={"sid": "1", "client_secret": "abc", "mxid": "@a:hs.test"},
    )
    assert v2.status == 401


def test_unbind_with_session(menv: Env, sydent: Sydent, hs: Homeserver) -> None:
    secret = "secret-unbind"
    address = "unbind@example.test"
    sid = validated_email_session(sydent, menv, address, secret)
    bound = sydent.request(
        "POST",
        f"{V1}/3pid/bind",
        json_body={"sid": sid, "client_secret": secret, "mxid": hs.mxid("u")},
    )
    assert bound.status == 200
    threepid = {"medium": "email", "address": address}

    mismatch = sydent.request(
        "POST",
        UNBIND,
        json_body={
            "sid": sid,
            "client_secret": secret,
            "mxid": hs.mxid("u"),
            "threepid": {"medium": "email", "address": "else@example.test"},
        },
    )
    assert mismatch.status == 403
    assert mismatch.json()["errcode"] == "M_FORBIDDEN"

    wrong = sydent.request(
        "POST",
        UNBIND,
        json_body={
            "sid": sid,
            "client_secret": "nope",
            "mxid": hs.mxid("u"),
            "threepid": threepid,
        },
    )
    assert wrong.status == 401
    assert wrong.json()["errcode"] == "M_NO_VALID_SESSION"

    resp = sydent.request(
        "POST",
        f"{V1}/3pid/unbind",
        json_body={
            "sid": sid,
            "client_secret": secret,
            "mxid": hs.mxid("u"),
            "threepid": threepid,
        },
    )
    assert resp.status == 200, resp
    assert resp.json() == {}
    assert v1_lookup(sydent, "email", address) == {}


def test_unbind_with_unvalidated_session_keeps_binding(
    menv: Env, sydent: Sydent
) -> None:
    address = "stays@example.test"
    internal_bind(sydent, "email", address, "@stays:hs.test")
    secret = "secret-unbind-unvalidated"
    sid, _ = request_email_token(sydent, menv, address, secret)
    try:
        resp = sydent.request(
            "POST",
            UNBIND,
            json_body={
                "sid": sid,
                "client_secret": secret,
                "mxid": "@stays:hs.test",
                "threepid": {"medium": "email", "address": address},
            },
            timeout=1.0,
        )
        assert resp.status == 403, resp
    except TimeoutError:
        # Existing behaviour: Sydent never finishes this response.
        pass
    assert v1_lookup(sydent, "email", address)["mxid"] == "@stays:hs.test"


@pytest.mark.parametrize(
    "body, status, errcode",
    [
        (b"{", 400, "M_BAD_JSON"),
        (b'{"mxid": "@a:hs.test"}', 400, "M_MISSING_PARAMS"),
        (
            b'{"mxid": "@a:hs.test", "threepid": {"medium": "email"}}',
            400,
            "M_MISSING_PARAMS",
        ),
        (
            b'{"mxid": "@a:hs.test", "threepid": {"medium": "email", "address": "a@b.test"},'
            b' "sid": "1", "client_secret": "bad secret"}',
            400,
            "M_INVALID_PARAM",
        ),
        (
            b'{"mxid": "@a:hs.test", "threepid": {"medium": "email", "address": "a@b.test"}}',
            401,
            "M_FORBIDDEN",
        ),
    ],
)
def test_unbind_errors(sydent: Sydent, body: bytes, status: int, errcode: str) -> None:
    resp = sydent.request(
        "POST", UNBIND, body=body, headers={"Content-Type": "application/json"}
    )
    assert resp.status == status, resp
    assert resp.json()["errcode"] == errcode


@pytest.mark.parametrize("case", ["valid", "other-origin", "tampered"])
def test_unbind_with_homeserver_signature(
    menv: Env, sydent: Sydent, hs: Homeserver, case: str
) -> None:
    address = f"signed-{case}@example.test"
    mxid = hs.mxid(f"signed-{case}")
    internal_bind(sydent, "email", address, mxid)
    signer = Homeserver(menv, f"signer-{case}.test") if case == "other-origin" else hs
    content = {"mxid": mxid, "threepid": {"medium": "email", "address": address}}
    signed = {"tampered": True} if case == "tampered" else content
    header = signer.x_matrix("POST", UNBIND, SERVER_NAME, signed)
    resp = sydent.request(
        "POST", UNBIND, json_body=content, headers={"Authorization": header}
    )
    if case == "valid":
        assert resp.status == 200, resp
        assert resp.json() == {}
        assert v1_lookup(sydent, "email", address) == {}
    elif case == "other-origin":
        assert resp.status == 403, resp
        assert resp.json() == {
            "errcode": "M_FORBIDDEN",
            "error": "Origin server name does not match mxid",
        }
        assert v1_lookup(sydent, "email", address)["mxid"] == mxid
    else:
        assert resp.status == 401, resp
        assert resp.json()["errcode"] == "M_FORBIDDEN"
        assert resp.json()["error"].startswith(
            f"Unable to verify signature for {hs.server_name}:"
        )
        assert v1_lookup(sydent, "email", address)["mxid"] == mxid
    fetch = wait_until(lambda: signer.fake.requests_to(KEYS))[0]
    assert fetch.method == "GET"
    assert fetch.headers["host"] == signer.server_name
    assert fetch.conn.sni == signer.host


def test_unbind_signature_with_invalid_origin(sydent: Sydent) -> None:
    content = {
        "mxid": "@a:hs.test",
        "threepid": {"medium": "email", "address": "a@b.test"},
    }
    header = 'X-Matrix origin=bad_name,key="ed25519:k1",sig="abc"'
    resp = sydent.request(
        "POST", UNBIND, json_body=content, headers={"Authorization": header}
    )
    assert resp.status == 400
    assert resp.json()["errcode"] == "M_INVALID_PARAM"


def test_onbind_retried_after_failure(menv: Env, sydent: Sydent) -> None:
    flaky = Homeserver(menv, "flaky.test")
    calls: List[float] = []

    def onbind(req: Request) -> Reply:
        calls.append(time.monotonic())
        return json_reply({}, 500 if len(calls) == 1 else 200)

    flaky.fake.route("POST", ONBIND, onbind)
    internal_bind(sydent, "email", "flaky@example.test", flaky.mxid("f"))
    wait_until(lambda: len(calls) >= 2, timeout=10)
    # The first retry is scheduled 1 s (2**0) after the failure. Checking for
    # half of that tells a delayed retry from an immediate one.
    assert calls[1] - calls[0] >= 0.5
