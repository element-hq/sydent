# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Phone number validation through a fake OpenMarket API, and its ratelimits."""

from __future__ import annotations

import base64
import re
from typing import Any, Dict, List, Optional

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.fakes import FakeHTTPServer, Reply, Request, json_reply, wait_until
from tests.blackbox.flows import V1, V2, Homeserver, auth, register
from tests.blackbox.harness import Config, Sydent

OPENMARKET_HOST = "smsc.openmarket.com"
OPENMARKET_PATH = "/sms/v4/mt"
REQUEST_V1 = f"{V1}/validate/msisdn/requestToken"
SUBMIT_V1 = f"{V1}/validate/msisdn/submitToken"


def openmarket(env: Env) -> FakeHTTPServer:
    """A fake OpenMarket answering 202, or 400 for numbers ending in 999, or a
    redirect for numbers ending in 998."""
    fake = env.fake([OPENMARKET_HOST])

    def send(req: Request) -> Reply:
        dest = req.json()["mobileTerminate"]["destination"]["address"]
        if dest.endswith("998"):
            return Reply(302, b"", {"Location": f"https://{OPENMARKET_HOST}/moved"})
        if dest.endswith("999"):
            return json_reply({"error": {"code": 1, "description": "nope"}}, 400)
        return Reply(
            202,
            b"",
            {
                "Location": f"https://{OPENMARKET_HOST}{OPENMARKET_PATH}/ticket-1",
                "X-Request-Id": "req-1",
            },
        )

    fake.route("POST", OPENMARKET_PATH, send)
    env.host(OPENMARKET_HOST, fake, 443)
    return fake


def sms_config(**sms: str) -> Config:
    base = {"username": "omuser", "password": "ompass"}
    base.update(sms)
    return {"sms": base}


def request_token(
    sydent: Sydent,
    phone: str = "07700900123",
    country: str = "GB",
    secret: str = "secret",
    path: str = REQUEST_V1,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    return sydent.request(
        "POST",
        path,
        json_body={
            "phone_number": phone,
            "country": country,
            "client_secret": secret,
            "send_attempt": 1,
        },
        headers=headers,
    )


def sms_token(fake: FakeHTTPServer, index: int = -1) -> str:
    content = fake.requests_to(OPENMARKET_PATH)[index].json()["mobileTerminate"]
    match = re.fullmatch(r"Your code is (\S+)", content["message"]["content"])
    assert match, content
    return match.group(1)


@pytest.fixture(scope="module")
def om(menv: Env) -> FakeHTTPServer:
    return openmarket(menv)


@pytest.fixture(scope="module")
def sydent(menv: Env, om: FakeHTTPServer) -> Sydent:
    return menv.sydent(
        sms_config(
            **{
                "originators.33": "long:33123456789",
                "smsrule.49": "reject",
                "msisdn.ratelimit.burst": "1000",
                "country.ratelimit.burst": "1000",
            }
        )
    )


def test_request_token_sends_sms(sydent: Sydent, om: FakeHTTPServer) -> None:
    before = len(om.requests_to(OPENMARKET_PATH))
    resp = request_token(sydent, "07700900123", "GB", "secret1")
    assert resp.status == 200, resp
    body = resp.json()
    assert body == {
        "success": True,
        "sid": body["sid"],
        "msisdn": "447700900123",
        "intl_fmt": "+44 7700 900123",
    }
    sent = om.requests_to(OPENMARKET_PATH)[before:]
    assert len(sent) == 1
    req = sent[0]
    assert req.conn.sni == OPENMARKET_HOST
    assert req.headers["host"] == OPENMARKET_HOST
    assert (
        req.headers["authorization"]
        == "Basic " + base64.b64encode(b"omuser:ompass").decode()
    )
    assert req.headers["content-type"] == "application/json"
    token = sms_token(om)
    assert re.fullmatch(r"\d{6}", token)
    assert req.json() == {
        "mobileTerminate": {
            "message": {"content": f"Your code is {token}", "type": "text"},
            "destination": {"address": "447700900123"},
            "source": {"ton": 5, "address": "Matrix"},
        }
    }


def test_configured_originator(sydent: Sydent, om: FakeHTTPServer) -> None:
    resp = request_token(sydent, "0612345678", "FR", "secret2")
    assert resp.status == 200, resp
    source = om.requests_to(OPENMARKET_PATH)[-1].json()["mobileTerminate"]["source"]
    assert source == {"ton": 1, "address": "33123456789"}


def test_sms_rule_reject(sydent: Sydent, om: FakeHTTPServer) -> None:
    before = len(om.requests_to(OPENMARKET_PATH))
    resp = request_token(sydent, "015112345678", "DE", "secret3")
    assert resp.status == 400
    assert resp.json()["errcode"] == "M_DESTINATION_REJECTED"
    assert len(om.requests_to(OPENMARKET_PATH)) == before


def test_invalid_phone_number(sydent: Sydent) -> None:
    resp = request_token(sydent, "notanumber", "GB", "secret4")
    assert resp.status == 400
    assert resp.json() == {
        "errcode": "M_INVALID_PHONE_NUMBER",
        "error": "Invalid phone number",
    }


@pytest.mark.parametrize(
    "body",
    [
        {
            "phone_number": "07700900123",
            "country": "GB",
            "client_secret": "s",
            "send_attempt": "x",
        },
        {
            "phone_number": "07700900123",
            "country": "GB",
            "client_secret": "a b",
            "send_attempt": 1,
        },
    ],
)
def test_request_token_invalid_params(sydent: Sydent, body: Dict[str, Any]) -> None:
    resp = sydent.request("POST", REQUEST_V1, json_body=body)
    assert resp.status == 400
    assert resp.json()["errcode"] == "M_INVALID_PARAM"


def test_openmarket_error(sydent: Sydent) -> None:
    resp = request_token(sydent, "07700900999", "GB", "secret5")
    assert resp.status == 500
    assert resp.json() == {"errcode": "M_UNKNOWN", "error": "Internal Server Error"}


def test_openmarket_redirect_not_followed(sydent: Sydent, om: FakeHTTPServer) -> None:
    resp = request_token(sydent, "07700900998", "GB", "secret5b")
    assert resp.status == 500
    assert om.requests_to("/moved") == []


def test_openmarket_request_has_no_user_agent(
    sydent: Sydent, om: FakeHTTPServer
) -> None:
    assert request_token(sydent, "07700900124", "GB", "secret5c").status == 200
    # Existing behaviour: no User-Agent header is sent.
    assert "user-agent" not in om.requests_to(OPENMARKET_PATH)[-1].headers


def test_v2_request_token_requires_auth(sydent: Sydent) -> None:
    resp = request_token(sydent, path=f"{V2}/validate/msisdn/requestToken")
    assert resp.status == 401


def test_submit_token_success(sydent: Sydent, om: FakeHTTPServer) -> None:
    sid = request_token(sydent, "07700900200", "GB", "secret6").json()["sid"]
    resp = sydent.request(
        "POST",
        SUBMIT_V1,
        json_body={"sid": sid, "client_secret": "secret6", "token": sms_token(om)},
    )
    assert resp.status == 200
    assert resp.json() == {"success": True}


@pytest.mark.parametrize(
    "case, status, errcode",
    [
        # Existing behaviour: the errors for an unknown sid and a wrong token are
        # swapped.
        ("unknown_sid", 400, "M_INVALID_PARAM"),
        ("wrong_secret", 400, "M_INVALID_PARAM"),
        ("wrong_token", 404, "M_NO_VALID_SESSION"),
        ("invalid_secret", 400, "M_INVALID_PARAM"),
    ],
)
def test_submit_token_failures(
    sydent: Sydent, om: FakeHTTPServer, case: str, status: int, errcode: str
) -> None:
    secret = f"secret-{case.replace('_', '-')}"
    sid = request_token(sydent, "07700900300", "GB", secret).json()["sid"]
    body = {"sid": sid, "client_secret": secret, "token": sms_token(om)}
    if case == "unknown_sid":
        body["sid"] = "999999"
    elif case == "wrong_secret":
        body["client_secret"] = "othersecret"
    elif case == "wrong_token":
        body["token"] = "000000x"
    else:
        body["client_secret"] = "bad secret"
    resp = sydent.request("POST", SUBMIT_V1, json_body=body)
    assert resp.status == status, resp
    assert resp.json()["errcode"] == errcode
    if case != "invalid_secret":
        assert resp.json()["success"] is False


def test_submit_token_get_is_500(sydent: Sydent, om: FakeHTTPServer) -> None:
    sid = request_token(sydent, "07700900400", "GB", "secret7").json()["sid"]
    good = f"{SUBMIT_V1}?sid={sid}&client_secret=secret7&token={sms_token(om)}"
    bad = f"{SUBMIT_V1}?sid={sid}&client_secret=secret7&token=nope"
    # Existing behaviour: the GET handler returns str instead of bytes, which
    # always ends in a 500.
    assert sydent.request("GET", bad).status == 500
    assert sydent.request("GET", good).status == 500


def _statuses(
    sydent: Sydent, count: int, path: str = REQUEST_V1, **kwargs: Any
) -> List[int]:
    return [request_token(sydent, path=path, **kwargs).status for _ in range(count)]


def wait_until_accepted(sydent: Sydent, phone: str) -> None:
    """Retry until a request for `phone` is let through by the ratelimiters."""
    # At 4 Hz the limiters drain within a second. The extra time covers a slow
    # machine.
    wait_until(lambda: request_token(sydent, phone=phone).status == 200, timeout=5)


def test_msisdn_ratelimit_drains(env: Env) -> None:
    openmarket(env)
    sydent = env.sydent(
        sms_config(
            **{
                "msisdn.ratelimit.burst": "1",
                "msisdn.ratelimit.rate_hz": "4",
                "country.ratelimit.burst": "1000",
            }
        )
    )
    assert request_token(sydent).status == 200
    # Two requests well inside one drain period: at most one drain can happen.
    burst = [request_token(sydent) for _ in range(2)]
    limited = [r for r in burst if r.status == 429]
    assert limited, burst
    assert limited[0].json() == {
        "errcode": "M_UNKNOWN",
        "error": "Limit exceeded for this number",
    }
    wait_until_accepted(sydent, "07700900123")


def test_country_ratelimit_drains(env: Env) -> None:
    openmarket(env)
    sydent = env.sydent(
        sms_config(
            **{
                "msisdn.ratelimit.burst": "1000",
                "country.ratelimit.burst": "1",
                "country.ratelimit.rate_hz": "4",
            }
        )
    )
    phones = ["07700900501", "07700900502", "07700900503"]
    statuses = [request_token(sydent, phone=p).status for p in phones]
    assert statuses[0] == 200
    assert 429 in statuses[1:], statuses
    wait_until_accepted(sydent, "07700900504")


def test_ratelimits_are_per_endpoint_version(env: Env) -> None:
    openmarket(env)
    hs = Homeserver(env, "hs.test")
    sydent = env.sydent(
        sms_config(
            **{
                "msisdn.ratelimit.burst": "1",
                "msisdn.ratelimit.rate_hz": "0.001",
                "country.ratelimit.burst": "1000",
            }
        )
    )
    token = register(sydent, hs, "alice")
    v2 = f"{V2}/validate/msisdn/requestToken"
    assert _statuses(sydent, 2) == [200, 429]
    assert _statuses(sydent, 2, path=v2, headers=auth(token)) == [200, 429]
