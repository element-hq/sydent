# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Homeserver discovery for outbound federation requests.

Each case drives `/v2/account/register`, which fetches `/openid/userinfo` from
the homeserver named in the request, and records at the fake homeserver which
address and port were connected to, the TLS SNI and the Host header.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.fakes import (
    SRV,
    FakeHTTPServer,
    Reply,
    Response,
    TestCA,
    ipv6_available,
    json_reply,
)
from tests.blackbox.flows import USERINFO, V2
from tests.blackbox.harness import Sydent

WELL_KNOWN = "/.well-known/matrix/server"
needs_ipv6 = pytest.mark.skipif(not ipv6_available(), reason="no IPv6 loopback")


@pytest.fixture(scope="module")
def sydent(menv: Env) -> Sydent:
    return menv.sydent()


def homeserver(
    env: Env, server_name: str, cert_name: str, cert_ip: Optional[str] = None
) -> FakeHTTPServer:
    """A fake answering userinfo for users of `server_name`."""
    fake = env.fake([cert_name], cert_ips=[cert_ip] if cert_ip else None)
    fake.route("GET", USERINFO, json_reply({"sub": f"@u:{server_name}"}))
    return fake


def register(sydent: Sydent, server_name: str) -> Response:
    return sydent.request(
        "POST",
        f"{V2}/account/register",
        json_body={"matrix_server_name": server_name, "access_token": "tok"},
    )


def assert_reached(fake: FakeHTTPServer, sni: Optional[str], host_header: str) -> None:
    requests = fake.requests_to(USERINFO)
    assert len(requests) == 1, fake.connections
    req = requests[0]
    assert req.conn.sni == sni
    assert req.headers["host"] == host_header
    assert req.path == f"{USERINFO}?access_token=tok"


def test_explicit_port(menv: Env, sydent: Sydent) -> None:
    fake = homeserver(menv, "port.test:8449", "port.test")
    menv.host("port.test", fake, 8449)
    resp = register(sydent, "port.test:8449")
    assert resp.status == 200, resp
    assert_reached(fake, "port.test", "port.test:8449")


def test_request_has_no_user_agent(menv: Env, sydent: Sydent) -> None:
    fake = homeserver(menv, "headers.test:8448", "headers.test")
    menv.host("headers.test", fake, 8448)
    assert register(sydent, "headers.test:8448").status == 200
    [req] = fake.requests_to(USERINFO)
    # Existing behaviour: federation requests carry no User-Agent.
    assert "user-agent" not in req.headers
    assert req.method == "GET"
    assert req.body == b""


@pytest.mark.parametrize(
    "v6, port",
    [
        (False, ""),
        (False, ":8448"),
        pytest.param(True, "", marks=needs_ipv6),
        pytest.param(True, ":8448", marks=needs_ipv6),
    ],
    ids=["ipv4", "ipv4-port", "ipv6", "ipv6-port"],
)
def test_ip_literal(menv: Env, sydent: Sydent, v6: bool, port: str) -> None:
    """An IP literal is connected to on port 8448 unless given, without SNI."""
    address = menv.net.new_address(v6=v6)
    server_name = (f"[{address}]" if v6 else address) + port
    fake = homeserver(menv, server_name, "ip.test", address)
    menv.net.serve(address, 8448, fake)
    resp = register(sydent, server_name)
    assert resp.status == 200, resp
    assert_reached(fake, None, server_name)


def test_certificate_name_not_checked(menv: Env, sydent: Sydent) -> None:
    # Existing behaviour: only the chain is verified, not the name.
    fake = homeserver(menv, "mismatch.test:8448", "something-else.test")
    menv.host("mismatch.test", fake, 8448)
    resp = register(sydent, "mismatch.test:8448")
    assert resp.status == 200, resp
    assert_reached(fake, "mismatch.test", "mismatch.test:8448")


def test_untrusted_certificate_refused(
    menv: Env, sydent: Sydent, tmp_path: Path
) -> None:
    other_ca = TestCA(str(tmp_path / "other-ca"))
    fake = FakeHTTPServer(
        other_ca.issue("untrusted.test", ["untrusted.test"]), other_ca
    )
    fake.route("GET", USERINFO, json_reply({"sub": "@u:untrusted.test:8448"}))
    try:
        menv.host("untrusted.test", fake, 8448)
        resp = register(sydent, "untrusted.test:8448")
        assert resp.status == 500, resp
        assert resp.json()["error"].startswith(
            "Unable to contact the Matrix homeserver"
        )
        assert fake.requests == []
        assert fake.connections and fake.connections[0].tls_error
    finally:
        fake.stop()


def test_untrusted_certificate_accepted_without_verification(
    env: Env, tmp_path: Path
) -> None:
    other_ca = TestCA(str(tmp_path / "other-ca"))
    fake = FakeHTTPServer(other_ca.issue("noverify.test", ["noverify.test"]), other_ca)
    fake.route("GET", USERINFO, json_reply({"sub": "@u:noverify.test:8448"}))
    try:
        env.host("noverify.test", fake, 8448)
        sydent = env.sydent({"http": {"federation.verifycerts": "false"}})
        resp = register(sydent, "noverify.test:8448")
        assert resp.status == 200, resp
        assert_reached(fake, "noverify.test", "noverify.test:8448")
    finally:
        fake.stop()


def test_well_known_delegation_with_port(menv: Env, sydent: Sydent) -> None:
    wk = menv.fake(["wk.test"])
    wk.route("GET", WELL_KNOWN, json_reply({"m.server": "delegated.test:8450"}))
    menv.host("wk.test", wk, 443)
    fake = homeserver(menv, "wk.test", "delegated.test")
    menv.host("delegated.test", fake, 8450)
    resp = register(sydent, "wk.test")
    assert resp.status == 200, resp
    [wk_req] = wk.requests_to(WELL_KNOWN)
    assert wk_req.conn.sni == "wk.test"
    assert wk_req.headers["host"] == "wk.test"
    assert_reached(fake, "delegated.test", "delegated.test:8450")
    # The delegation is cached.
    assert register(sydent, "wk.test").status == 200
    assert len(wk.requests_to(WELL_KNOWN)) == 1
    assert len(fake.requests_to(USERINFO)) == 2


def test_well_known_delegation_then_srv(menv: Env, sydent: Sydent) -> None:
    wk = menv.fake(["wk2.test"])
    wk.route("GET", WELL_KNOWN, json_reply({"m.server": "deleg2.test"}))
    menv.host("wk2.test", wk, 443)
    menv.net.add_host("deleg2.test", menv.net.new_address())
    menv.dns.srv["_matrix-fed._tcp.deleg2.test"] = [SRV("target2.test", 8452)]
    fake = homeserver(menv, "wk2.test", "deleg2.test")
    menv.host("target2.test", fake, 8452)
    resp = register(sydent, "wk2.test")
    assert resp.status == 200, resp
    assert_reached(fake, "deleg2.test", "deleg2.test")
    assert "_matrix-fed._tcp.wk2.test" not in menv.dns.queried()


def test_well_known_redirect_followed(menv: Env, sydent: Sydent) -> None:
    wk = menv.fake(["wk3.test"])
    wk.route("GET", WELL_KNOWN, Reply(302, b"", {"Location": "https://wk3.test/moved"}))
    wk.route("GET", "/moved", json_reply({"m.server": "deleg3.test:8453"}))
    menv.host("wk3.test", wk, 443)
    fake = homeserver(menv, "wk3.test", "deleg3.test")
    menv.host("deleg3.test", fake, 8453)
    resp = register(sydent, "wk3.test")
    assert resp.status == 200, resp
    assert_reached(fake, "deleg3.test", "deleg3.test:8453")


@pytest.mark.parametrize(
    "case", ["not-found", "invalid-json", "no-m-server", "wrong-cert-name"]
)
def test_well_known_unusable_falls_back(menv: Env, sydent: Sydent, case: str) -> None:
    name = f"wkbad-{case}.test"
    wk = menv.fake(["other.test" if case == "wrong-cert-name" else name])
    if case == "invalid-json":
        wk.route("GET", WELL_KNOWN, Reply(200, b"{"))
    elif case == "no-m-server":
        wk.route("GET", WELL_KNOWN, json_reply({"x": 1}))
    elif case == "wrong-cert-name":
        wk.route("GET", WELL_KNOWN, json_reply({"m.server": "elsewhere.test:1"}))
    fake = homeserver(menv, name, name)
    menv.host(name, wk, 443)
    menv.net.serve(menv.net.hosts[name][0], 8448, fake)
    resp = register(sydent, name)
    assert resp.status == 200, resp
    assert_reached(fake, name, name)


def test_srv_record(menv: Env, sydent: Sydent) -> None:
    menv.net.add_host("srv.test", menv.net.new_address())
    menv.dns.srv["_matrix-fed._tcp.srv.test"] = [SRV("target.test", 8451)]
    # The certificate is valid for the server name only, not for the SRV target.
    fake = homeserver(menv, "srv.test", "srv.test")
    menv.host("target.test", fake, 8451)
    resp = register(sydent, "srv.test")
    assert resp.status == 200, resp
    assert_reached(fake, "srv.test", "srv.test")
    queried = [q for q in menv.dns.queried() if q.endswith(".srv.test")]
    assert queried == ["_matrix-fed._tcp.srv.test"]


def test_legacy_srv_record(menv: Env, sydent: Sydent) -> None:
    menv.net.add_host("legacy.test", menv.net.new_address())
    menv.dns.srv["_matrix._tcp.legacy.test"] = [SRV("ltarget.test", 8454)]
    fake = homeserver(menv, "legacy.test", "legacy.test")
    menv.host("ltarget.test", fake, 8454)
    resp = register(sydent, "legacy.test")
    assert resp.status == 200, resp
    assert_reached(fake, "legacy.test", "legacy.test")
    queried = [q for q in menv.dns.queried() if q.endswith("legacy.test")]
    assert queried == ["_matrix-fed._tcp.legacy.test", "_matrix._tcp.legacy.test"]


def test_srv_prefers_matrix_fed_and_lowest_priority(menv: Env, sydent: Sydent) -> None:
    menv.net.add_host("prio.test", menv.net.new_address())
    fake = homeserver(menv, "prio.test", "prio.test")
    decoy = homeserver(menv, "prio.test", "prio.test")
    menv.host("best.test", fake, 8455)
    menv.host("worse.test", decoy, 8456)
    menv.host("legacy-prio.test", decoy, 8457)
    menv.dns.srv["_matrix-fed._tcp.prio.test"] = [
        SRV("worse.test", 8456, priority=10, weight=100),
        SRV("best.test", 8455, priority=1, weight=0),
    ]
    menv.dns.srv["_matrix._tcp.prio.test"] = [SRV("legacy-prio.test", 8457)]
    for _ in range(3):
        assert register(sydent, "prio.test").status == 200
    assert len(fake.requests_to(USERINFO)) == 3
    assert decoy.connections == []


@pytest.mark.parametrize("dns_case", ["nxdomain", "nodata"])
def test_no_srv_record_uses_port_8448(menv: Env, sydent: Sydent, dns_case: str) -> None:
    name = f"no-srv-{dns_case}.test"
    fake = homeserver(menv, name, name)
    menv.host(name, fake, 8448)
    if dns_case == "nodata":
        menv.dns.nodata.update({f"_matrix-fed._tcp.{name}", f"_matrix._tcp.{name}"})
    resp = register(sydent, name)
    assert resp.status == 200, resp
    assert_reached(fake, name, name)


def test_srv_target_dot_means_unavailable(menv: Env, sydent: Sydent) -> None:
    fake = homeserver(menv, "dot.test", "dot.test")
    menv.host("dot.test", fake, 8448)
    menv.dns.srv["_matrix-fed._tcp.dot.test"] = [SRV(".", 0)]
    resp = register(sydent, "dot.test")
    assert resp.status == 500, resp
    assert resp.json()["errcode"] == "M_UNKNOWN"
    assert fake.connections == []


def test_srv_lookup_failure(menv: Env, sydent: Sydent) -> None:
    fake = homeserver(menv, "servfail.test", "servfail.test")
    menv.host("servfail.test", fake, 8448)
    menv.dns.servfail.add("_matrix-fed._tcp.servfail.test")
    resp = register(sydent, "servfail.test")
    assert resp.status == 500, resp
    assert resp.json()["errcode"] == "M_UNKNOWN"
    assert fake.connections == []


def test_federation_redirect_not_followed(menv: Env, sydent: Sydent) -> None:
    target = homeserver(menv, "redir.test:8448", "redir-target.test")
    menv.host("redir-target.test", target, 8448)
    fake = menv.fake(["redir.test"])
    fake.route(
        "GET",
        USERINFO,
        Reply(302, b"", {"Location": f"https://redir-target.test:8448{USERINFO}"}),
    )
    menv.host("redir.test", fake, 8448)
    resp = register(sydent, "redir.test:8448")
    assert resp.status == 500, resp
    assert len(fake.requests_to(USERINFO)) == 1
    assert target.connections == []
