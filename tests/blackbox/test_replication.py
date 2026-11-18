# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Replication between Sydents over mutual TLS.

Every case runs twice: with `replication.https.cacert` set to the test CA, and
without it, where the test CA is the platform trust store of the process.
Peers are configured in the database, which is the only way Sydent offers.
"""

from __future__ import annotations

import json
import sqlite3
import ssl
import time
from typing import Any, Dict, Optional

import pytest
import signedjson.key
import signedjson.sign
from unpaddedbase64 import encode_base64

from tests.blackbox.environment import Env
from tests.blackbox.fakes import Response, TestCA, http_request, wait_until
from tests.blackbox.flows import V1, internal_bind, pubkey, v1_lookup, verify_signed
from tests.blackbox.harness import Sydent

PUSH = "/_matrix/identity/replicate/v1/push"
PEER_NAME = "peer.test"


def add_peer(sydent: Sydent, name: str, key: str) -> None:
    db = sqlite3.connect(sydent.db_path)
    try:
        db.execute(
            "INSERT INTO peers (name, port, active) VALUES (?, NULL, 1)", (name,)
        )
        db.execute(
            "INSERT INTO peer_pubkeys (peername, alg, key) VALUES (?, 'ed25519', ?)",
            (name, key),
        )
        db.commit()
    finally:
        db.close()


def replicating_sydent(env: Env, name: str, ca_mode: str, **kwargs: Any) -> Sydent:
    cert = env.ca.issue(name, dns_names=[name], ip_addresses=["127.0.0.1"])
    config: Dict[str, Dict[str, str]] = {"http": {}}
    if ca_mode == "ca-file":
        config["http"]["replication.https.cacert"] = env.ca.cert_path
    return env.sydent(config, server_name=name, replication_cert=cert, **kwargs)


@pytest.fixture(scope="module", params=["system-trust", "ca-file"])
def ca_mode(request: pytest.FixtureRequest) -> str:
    mode: str = request.param
    return mode


@pytest.fixture(scope="module")
def peer_key() -> Any:
    return signedjson.key.generate_signing_key("0")


@pytest.fixture(scope="module")
def receiver(menv: Env, ca_mode: str, peer_key: Any) -> Sydent:
    sydent = replicating_sydent(menv, f"receiver-{ca_mode}.test", ca_mode)
    add_peer(sydent, PEER_NAME, encode_base64(peer_key.verify_key.encode()))
    return sydent


def signed_assoc(
    key: Any, signer: str, address: str, mxid: Optional[str]
) -> Dict[str, Any]:
    now = int(time.time() * 1000)
    assoc = {
        "medium": "email",
        "address": address,
        "mxid": mxid,
        "ts": now,
        "not_before": now,
        "not_after": now + 10**9,
    }
    signed: Dict[str, Any] = signedjson.sign.sign_json(assoc, signer, key)
    return signed


def push(
    sydent: Sydent,
    ca: TestCA,
    client_cert: Optional[str],
    body: bytes,
    content_type: str = "application/json",
) -> Response:
    return http_request(
        sydent.replication_url,
        "POST",
        PUSH,
        body=body,
        headers={"Content-Type": content_type},
        ssl_context=ca.client_context(client_cert),
    )


def json_bytes(obj: Any) -> bytes:
    return json.dumps(obj).encode()


@pytest.fixture(scope="module")
def peer_cert(menv: Env) -> str:
    return menv.ca.issue(PEER_NAME, dns_names=[PEER_NAME])


def test_push_from_known_peer(
    menv: Env, receiver: Sydent, peer_key: Any, peer_cert: str
) -> None:
    assoc = signed_assoc(peer_key, PEER_NAME, "pushed@example.test", "@p:hs.test")
    resp = push(receiver, menv.ca, peer_cert, json_bytes({"sgAssocs": {"1": assoc}}))
    assert resp.status == 200, resp
    assert resp.json() == {"success": True}
    found = v1_lookup(receiver, "email", "pushed@example.test")
    assert found["mxid"] == "@p:hs.test"
    assert PEER_NAME in found["signatures"]


def test_push_deletion(
    menv: Env, receiver: Sydent, peer_key: Any, peer_cert: str
) -> None:
    add = signed_assoc(peer_key, PEER_NAME, "deleted@example.test", "@d:hs.test")
    assert (
        push(receiver, menv.ca, peer_cert, json_bytes({"sgAssocs": {"10": add}})).status
        == 200
    )
    delete = signed_assoc(peer_key, PEER_NAME, "deleted@example.test", None)
    resp = push(receiver, menv.ca, peer_cert, json_bytes({"sgAssocs": {"11": delete}}))
    assert resp.status == 200, resp
    assert v1_lookup(receiver, "email", "deleted@example.test") == {}


def test_push_bad_signature_rolls_back(
    menv: Env, receiver: Sydent, peer_key: Any, peer_cert: str
) -> None:
    good = signed_assoc(peer_key, PEER_NAME, "good@example.test", "@g:hs.test")
    other_key = signedjson.key.generate_signing_key("0")
    bad = signed_assoc(other_key, PEER_NAME, "bad@example.test", "@b:hs.test")
    resp = push(
        receiver, menv.ca, peer_cert, json_bytes({"sgAssocs": {"20": good, "21": bad}})
    )
    assert resp.status == 400, resp
    assert resp.json() == {
        "errcode": "M_VERIFICATION_FAILED",
        "error": "Verification failed for one or more associations",
        "failed_ids": ["21"],
    }
    assert v1_lookup(receiver, "email", "good@example.test") == {}


def test_unknown_peer_cn_is_403(menv: Env, receiver: Sydent, peer_key: Any) -> None:
    cert = menv.ca.issue("stranger.test", dns_names=["stranger.test"])
    assoc = signed_assoc(peer_key, PEER_NAME, "stranger@example.test", "@s:hs.test")
    resp = push(receiver, menv.ca, cert, json_bytes({"sgAssocs": {"1": assoc}}))
    assert resp.status == 403, resp
    assert resp.json() == {
        "errcode": "M_UNKNOWN_PEER",
        "error": "This peer is not known to this server",
    }


def assert_handshake_refused(sydent: Sydent, ctx: ssl.SSLContext) -> None:
    """Fail unless the server rejects the TLS session before answering."""
    try:
        resp = http_request(
            sydent.replication_url,
            "POST",
            PUSH,
            body=b"{}",
            headers={"Content-Type": "application/json"},
            ssl_context=ctx,
        )
    except OSError:
        return
    raise AssertionError(f"request was answered: {resp}")


def test_peer_without_client_certificate_refused(menv: Env, receiver: Sydent) -> None:
    assert_handshake_refused(receiver, menv.ca.client_context(None))


def test_peer_with_untrusted_certificate_refused(
    menv: Env, receiver: Sydent, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other = TestCA(str(tmp_path_factory.mktemp("other-ca")))
    ctx = menv.ca.client_context(None)
    ctx.load_cert_chain(other.issue(PEER_NAME, dns_names=[PEER_NAME]))
    assert_handshake_refused(receiver, ctx)


@pytest.mark.parametrize(
    "content_type, body, status, errcode",
    [
        ("text/plain", b'{"sgAssocs": {}}', 400, "M_NOT_JSON"),
        # Existing behaviour: parameters on the media type are rejected.
        ("application/json; charset=utf-8", b'{"sgAssocs": {}}', 400, "M_NOT_JSON"),
        ("application/json", b"{", 400, "M_BAD_JSON"),
        ("application/json", b"{}", 400, "M_BAD_JSON"),
    ],
)
def test_push_request_validation(
    menv: Env,
    receiver: Sydent,
    peer_cert: str,
    content_type: str,
    body: bytes,
    status: int,
    errcode: str,
) -> None:
    resp = push(receiver, menv.ca, peer_cert, body, content_type)
    assert resp.status == status, resp
    assert resp.json()["errcode"] == errcode


def test_replication_between_two_sydents(env: Env, ca_mode: str) -> None:
    origin = replicating_sydent(
        env, "origin.test", ca_mode, internal_api=True, start=False
    )
    target = replicating_sydent(env, "target.test", ca_mode, start=False)
    origin.extra_config["peer.target.test"] = {
        "base_replication_url": target.replication_url
    }
    origin.start()
    target.start()
    add_peer(origin, "target.test", pubkey(target))
    add_peer(target, "origin.test", pubkey(origin))

    internal_bind(origin, "email", "replicated@example.test", "@r:hs.test")
    # Pushes run at startup and then every 10 seconds.
    origin.restart()
    found = wait_until(
        lambda: v1_lookup(target, "email", "replicated@example.test"), timeout=15
    )
    assert found["mxid"] == "@r:hs.test"
    verify_signed(found, "origin.test", pubkey(origin))
    assert target.request("GET", f"{V1}/lookup?medium=email&address=x").status == 200
