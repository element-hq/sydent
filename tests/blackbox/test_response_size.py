# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Size caps on outbound responses.

Sydent must answer once a response passes its cap, without waiting for the rest
of the body. Whether it then closes the connection or reads the rest is not
pinned: Sydent drains the connection today, and closing it is what the code
intends.
"""

from __future__ import annotations

import json
import threading

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.fakes import (
    FakeHTTPServer,
    Reply,
    Response,
    json_reply,
    stream_of,
)
from tests.blackbox.flows import USERINFO, V2
from tests.blackbox.harness import Sydent

# The cap that registerservlet.py passes to get_json for userinfo.
USERINFO_CAP = 5 * 1024
# Far more than any cap. The fake holds the body back after PAUSE_AFTER bytes
# until the test ends, so a reader that waits for the whole body cannot answer
# in time.
STREAMED = 64 * 1024 * 1024
PAUSE_AFTER = 1024 * 1024
# The size of one chunk from fakes.stream_of.
CHUNK = 64 * 1024


@pytest.fixture(scope="module")
def sydent(menv: Env) -> Sydent:
    return menv.sydent()


def homeserver(env: Env, name: str, reply: Reply) -> FakeHTTPServer:
    fake = env.fake([name])
    fake.route("GET", USERINFO, reply)
    env.host(name, fake, 8448)
    return fake


def register(sydent: Sydent, server_name: str) -> Response:
    return sydent.request(
        "POST",
        f"{V2}/account/register",
        json_body={"matrix_server_name": server_name, "access_token": "tok"},
    )


def body_of_size(server_name: str, size: int) -> bytes:
    base = {"sub": f"@u:{server_name}", "pad": ""}
    pad = size - len(json.dumps(base).encode())
    base["pad"] = "x" * pad
    body = json.dumps(base).encode()
    assert len(body) == size
    return body


@pytest.mark.parametrize("declared_length", [False, True])
def test_answer_does_not_wait_for_body_over_cap(
    menv: Env, sydent: Sydent, declared_length: bool
) -> None:
    """Sydent answers while the fake is still holding back most of the body."""
    gate = threading.Event()
    name = f"stream-{declared_length}.test".lower()
    body = stream_of(STREAMED, pause_after=PAUSE_AFTER, gate=gate)
    fake = homeserver(
        menv,
        name,
        Reply(200, body, content_length=STREAMED if declared_length else None),
    )
    try:
        resp = register(sydent, f"{name}:8448")
        assert resp.status == 500, resp
        assert resp.json()["errcode"] == "M_UNKNOWN"
        assert not fake.streams[0].finished
        assert fake.streams[0].bytes_sent <= PAUSE_AFTER + CHUNK
    finally:
        gate.set()


@pytest.mark.parametrize("size, status", [(USERINFO_CAP - 1, 200), (USERINFO_CAP, 500)])
def test_userinfo_cap_boundary(
    menv: Env, sydent: Sydent, size: int, status: int
) -> None:
    name = f"size{size}.test"
    homeserver(menv, name, Reply(200, body_of_size(f"{name}:8448", size)))
    resp = register(sydent, f"{name}:8448")
    # Existing behaviour: a body of exactly the cap is refused.
    assert resp.status == status, resp


def test_well_known_over_cap_is_ignored(menv: Env, sydent: Sydent) -> None:
    wk = menv.fake(["bigwk.test"])
    # WELL_KNOWN_MAX_SIZE in matrixfederationagent.py is 50 KiB.
    big = {"m.server": "elsewhere.test:1", "pad": "x" * (50 * 1024)}
    wk.route("GET", "/.well-known/matrix/server", json_reply(big))
    fake = menv.fake(["bigwk.test"])
    fake.route("GET", USERINFO, json_reply({"sub": "@u:bigwk.test"}))
    address = menv.host("bigwk.test", wk, 443)
    menv.net.serve(address, 8448, fake)
    resp = register(sydent, "bigwk.test")
    assert resp.status == 200, resp
    assert len(fake.requests_to(USERINFO)) == 1
