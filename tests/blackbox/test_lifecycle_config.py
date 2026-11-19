# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Process lifecycle and configuration switches."""

from __future__ import annotations

import signal
import socket
import sqlite3
import time

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.fakes import http_request
from tests.blackbox.flows import V1, V2, request_email_token, token_from_mail
from tests.blackbox.harness import Sydent, free_port

# Arbitrary, well above the time Sydent takes to exit after a signal.
SHUTDOWN_DEADLINE = 5.0


def refuses_connections(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return False
    except OSError:
        return True


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_clean_shutdown_on_signal(env: Env, sig: int) -> None:
    sydent = env.sydent(internal_api=True)
    assert sydent.proc is not None
    started = time.monotonic()
    sydent.proc.send_signal(sig)
    code = sydent.proc.wait(SHUTDOWN_DEADLINE)
    assert time.monotonic() - started < SHUTDOWN_DEADLINE
    assert code == 0, sydent.log_tail()
    assert refuses_connections(sydent.client_port)
    assert sydent.internal_port is not None
    assert refuses_connections(sydent.internal_port)


def test_pidfile(env: Env) -> None:
    sydent = env.sydent()
    assert sydent.proc is not None
    with open(f"{sydent.directory}/sydent.pid") as f:
        assert f.read() == f"{sydent.proc.pid}\n"


@pytest.fixture(scope="module")
def v1_disabled(menv: Env) -> Sydent:
    return menv.sydent({"general": {"enable_v1_access": "false"}})


@pytest.mark.parametrize(
    "path",
    [
        V1,
        f"{V1}/lookup?medium=email&address=a@b.test",
        f"{V1}/pubkey/ed25519:0",
        f"{V1}/pubkey/isvalid?public_key=x",
        f"{V1}/3pid/getValidated3pid?sid=1&client_secret=a",
    ],
)
def test_v1_disabled_get_is_404(v1_disabled: Sydent, path: str) -> None:
    assert v1_disabled.request("GET", path).status == 404


@pytest.mark.parametrize(
    "path",
    [
        "/validate/email/requestToken",
        "/validate/msisdn/requestToken",
        "/3pid/bind",
        "/3pid/unbind",
        "/store-invite",
        "/sign-ed25519",
        "/bulk_lookup",
    ],
)
def test_v1_disabled_post_is_404(v1_disabled: Sydent, path: str) -> None:
    assert v1_disabled.request("POST", f"{V1}{path}", json_body={}).status == 404


def test_v1_disabled_keeps_v2(v1_disabled: Sydent) -> None:
    sydent = v1_disabled
    assert sydent.request("GET", V2).status == 200
    assert sydent.request("GET", f"{V2}/pubkey/ed25519:0").status == 200
    assert sydent.request("GET", "/_matrix/identity/versions").status == 200


def test_v1_associations_disabled(env: Env) -> None:
    sydent = env.sydent({"general": {"enable_v1_associations": "false"}})
    bind = sydent.request(
        "POST",
        f"{V1}/3pid/bind",
        json_body={"sid": "1", "client_secret": "a", "mxid": "@a:b.test"},
    )
    assert bind.status == 404
    lookup = sydent.request("GET", f"{V1}/lookup?medium=email&address=a@b.test")
    assert lookup.status == 200


def _email_request(sydent: Sydent, address: str, forwarded_for: str) -> int:
    return sydent.request(
        "POST",
        f"{V1}/validate/email/requestToken",
        json_body={"email": address, "client_secret": "s", "send_attempt": 1},
        headers={"X-Forwarded-For": forwarded_for},
    ).status


def test_x_forwarded_for_obeyed(env: Env) -> None:
    sydent = env.sydent(
        {
            "http": {"obey_x_forwarded_for": "true"},
            "email": {"email.ratelimit_sender.burst": "1"},
        }
    )
    # v1 requestToken is ratelimited per client address.
    assert _email_request(sydent, "a@b.test", "192.0.2.1") == 200
    assert _email_request(sydent, "b@b.test", "192.0.2.2") == 200
    assert _email_request(sydent, "c@b.test", "192.0.2.1") == 429
    # Existing behaviour: the first X-Forwarded-For header is used verbatim, so
    # a list of addresses counts as a client of its own.
    assert _email_request(sydent, "d@b.test", "192.0.2.3, 192.0.2.1") == 200


def test_x_forwarded_for_ignored_by_default(env: Env) -> None:
    sydent = env.sydent({"email": {"email.ratelimit_sender.burst": "1"}})
    assert _email_request(sydent, "a@b.test", "192.0.2.1") == 200
    assert _email_request(sydent, "b@b.test", "192.0.2.2") == 429


def test_old_sessions_removed_at_startup(env: Env) -> None:
    sydent = env.sydent()
    sid, mail = request_email_token(sydent, env, "old@example.test", "oldsecret")
    db = sqlite3.connect(sydent.db_path)
    try:
        db.execute(
            "UPDATE threepid_validation_sessions SET mtime = 1 WHERE id = ?", (sid,)
        )
        db.commit()
    finally:
        db.close()
    sydent.restart()
    resp = sydent.request(
        "POST",
        f"{V1}/validate/email/submitToken",
        json_body={
            "sid": sid,
            "client_secret": "oldsecret",
            "token": token_from_mail(mail),
        },
    )
    assert resp.status == 200
    # An expired but still stored session would answer M_SESSION_EXPIRED.
    assert resp.json()["errcode"] == "M_INVALID_PARAM"


def test_request_metrics_labels(env: Env) -> None:
    port = free_port()
    sydent = env.sydent(
        {"general": {"prometheus_port": str(port), "prometheus_addr": "127.0.0.1"}},
        internal_api=True,
    )
    sydent.request("GET", "/_matrix/identity/versions")
    sydent.request("GET", f"{V2}/hash_details")
    sydent.request("GET", "/_matrix/identity/nope")
    sydent.request("OPTIONS", f"{V2}/lookup")
    http_request(
        sydent.internal_url,
        "POST",
        "/_matrix/identity/internal/bind",
        json_body={"medium": "email", "address": "m@b.test", "mxid": "@m:b.test"},
    )
    metrics = http_request(f"http://127.0.0.1:{port}", "GET", "/metrics")
    assert metrics.status == 200
    samples = {
        line.rsplit(" ", 1)[0]: float(line.rsplit(" ", 1)[1])
        for line in metrics.body.decode().splitlines()
        if line.startswith("sydent_http_received_requests_total{")
    }
    # Existing behaviour: the method label holds the repr of a bytes object.
    expected = {
        'sydent_http_received_requests_total{method="b\'GET\'",servlet="VersionsServlet"}': 1.0,
        'sydent_http_received_requests_total{method="b\'GET\'",servlet="HashDetailsServlet"}': 1.0,
        'sydent_http_received_requests_total{method="b\'OPTIONS\'",servlet="LookupV2Servlet"}': 1.0,
        'sydent_http_received_requests_total{method="b\'POST\'",servlet="AuthenticatedBindThreePidServlet"}': 1.0,
    }
    # The readiness probe of the harness also counts against CorsServlet.
    others = {k: v for k, v in samples.items() if 'servlet="CorsServlet"' not in k}
    assert others == expected
