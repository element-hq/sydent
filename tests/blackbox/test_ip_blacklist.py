# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""`ip.blacklist` / `ip.whitelist` on outbound requests.

The fakes listen on real loopback addresses, which the default blacklist covers;
a refused request must not open any connection to them.
"""

from __future__ import annotations

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.fakes import (
    FakeHTTPServer,
    Reply,
    Response,
    ipv6_available,
    json_reply,
    wait_until,
)
from tests.blackbox.flows import ONBIND, USERINFO, V2, internal_bind
from tests.blackbox.harness import Sydent

UNBIND_PATH = f"{V2}/3pid/unbind"
needs_ipv6 = pytest.mark.skipif(not ipv6_available(), reason="no IPv6 loopback")


@pytest.fixture(scope="module")
def sydent(menv: Env) -> Sydent:
    # An empty whitelist and the default blacklist.
    return menv.sydent({"general": {"ip.whitelist": ""}}, internal_api=True)


@pytest.fixture(scope="module")
def whitelisting_sydent(menv: Env) -> Sydent:
    return menv.sydent({"general": {"ip.whitelist": "127.0.0.1"}}, internal_api=True)


def userinfo_fake(env: Env, server_name: str) -> FakeHTTPServer:
    fake = env.fake(["loopback.test"], cert_ips=["127.0.0.1", "::1"])
    fake.route("GET", USERINFO, json_reply({"sub": f"@u:{server_name}"}))
    return fake


def register(sydent: Sydent, server_name: str) -> Response:
    return sydent.request(
        "POST",
        f"{V2}/account/register",
        json_body={"matrix_server_name": server_name, "access_token": "tok"},
    )


def assert_refused(resp: Response, fake: FakeHTTPServer) -> None:
    assert resp.status == 500, resp
    assert resp.json()["errcode"] == "M_UNKNOWN"
    assert resp.json()["error"].startswith("Unable to contact")
    assert fake.connections == []


@pytest.mark.parametrize(
    "literal, v6_port",
    [
        ("127.0.0.1", False),
        pytest.param("[::1]", True, marks=needs_ipv6),
        ("[::ffff:127.0.0.1]", False),
    ],
    ids=["ipv4", "ipv6", "ipv4-mapped"],
)
def test_blacklisted_literal_refused(
    menv: Env, sydent: Sydent, literal: str, v6_port: bool
) -> None:
    fake = userinfo_fake(menv, "x")
    port = fake.port6 if v6_port else fake.port4
    assert_refused(register(sydent, f"{literal}:{port}"), fake)


def test_name_resolving_to_blacklisted_address_refused(
    menv: Env, sydent: Sydent
) -> None:
    fake = userinfo_fake(menv, "x")
    menv.net.add_host("internal.test", "127.0.0.1")
    assert_refused(register(sydent, f"internal.test:{fake.port4}"), fake)


def test_test_net_address_refused_by_default(menv: Env, sydent: Sydent) -> None:
    fake = userinfo_fake(menv, "x")
    address = menv.net.new_address()
    menv.net.serve(address, 8448, fake)
    assert_refused(register(sydent, f"{address}:8448"), fake)


def test_whitelist_overrides_blacklist(menv: Env, whitelisting_sydent: Sydent) -> None:
    fake = userinfo_fake(menv, "placeholder")
    server_name = f"127.0.0.1:{fake.port4}"
    fake.route("GET", USERINFO, json_reply({"sub": f"@u:{server_name}"}))
    resp = register(whitelisting_sydent, server_name)
    assert resp.status == 200, resp
    assert len(fake.requests_to(USERINFO)) == 1


def test_name_with_one_blacklisted_address_refused_entirely(
    menv: Env, whitelisting_sydent: Sydent
) -> None:
    fake = userinfo_fake(menv, "x")
    address = menv.net.new_address()
    menv.net.serve(address, fake.port4, fake)
    menv.net.add_host("mixed.test", "127.0.0.1", address)
    # Existing behaviour: one blacklisted address fails the whole resolution.
    assert_refused(register(whitelisting_sydent, f"mixed.test:{fake.port4}"), fake)


@needs_ipv6
def test_redirect_to_blacklisted_address(
    menv: Env, whitelisting_sydent: Sydent
) -> None:
    target = userinfo_fake(menv, "x")
    fake = menv.fake(["loopback.test"], cert_ips=["127.0.0.1", "::1"])
    server_name = f"127.0.0.1:{fake.port4}"
    fake.route(
        "GET",
        USERINFO,
        Reply(302, b"", {"Location": f"https://[::1]:{target.port6}{USERINFO}"}),
    )
    resp = register(whitelisting_sydent, server_name)
    assert resp.status == 500, resp
    assert len(fake.requests_to(USERINFO)) == 1
    assert target.connections == []


def test_onbind_to_blacklisted_address_not_sent(
    menv: Env, whitelisting_sydent: Sydent
) -> None:
    blocked = userinfo_fake(menv, "x")
    address = menv.net.new_address()
    menv.net.serve(address, 8448, blocked)
    control = userinfo_fake(menv, "x")
    control.route("POST", ONBIND, json_reply({}))
    internal_bind(whitelisting_sydent, "email", "blocked@example.test", f"@u:{address}")
    internal_bind(
        whitelisting_sydent,
        "email",
        "control@example.test",
        f"@u:127.0.0.1:{control.port4}",
    )
    wait_until(lambda: control.requests_to(ONBIND))
    assert blocked.connections == []


def test_unbind_key_fetch_to_blacklisted_literal_refused(
    menv: Env, sydent: Sydent
) -> None:
    fake = userinfo_fake(menv, "x")
    origin = f"127.0.0.1:{fake.port4}"
    content = {
        "mxid": f"@u:{origin}",
        "threepid": {"medium": "email", "address": "a@b.test"},
    }
    resp = sydent.request(
        "POST",
        UNBIND_PATH,
        json_body=content,
        headers={
            "Authorization": f'X-Matrix origin={origin},key="ed25519:k1",sig="abc"'
        },
    )
    assert resp.status == 500, resp
    assert fake.connections == []
