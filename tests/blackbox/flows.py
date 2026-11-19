# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Multi-step client flows and a fake homeserver, shared by the test modules."""

from __future__ import annotations

import base64
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import signedjson.key
import signedjson.sign
from unpaddedbase64 import encode_base64

from tests.blackbox.environment import Env
from tests.blackbox.fakes import (
    FakeHTTPServer,
    Mail,
    Reply,
    Request,
    http_request,
    json_reply,
)
from tests.blackbox.harness import Sydent

V1 = "/_matrix/identity/api/v1"
V2 = "/_matrix/identity/v2"
USERINFO = "/_matrix/federation/v1/openid/userinfo"
ONBIND = "/_matrix/federation/v1/3pid/onbind"
KEYS = "/_matrix/key/v2/server/"

CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, POST, PUT, DELETE, OPTIONS",
    "access-control-allow-headers": "*",
}


def cors_headers(headers: List[Tuple[str, str]]) -> Dict[str, str]:
    return {k.lower(): v for k, v in headers if k.lower().startswith("access-control-")}


class Homeserver:
    """A fake homeserver reachable as `server_name` (`host:port`) over TLS.

    `/openid/userinfo?access_token=<localpart>` answers `@<localpart>:<server_name>`.
    """

    def __init__(self, env: Env, host: str, port: int = 8448) -> None:
        self.host = host
        self.server_name = f"{host}:{port}"
        self.fake: FakeHTTPServer = env.fake([host])
        self.address = env.host(host, self.fake, port)
        self.signing_key = signedjson.key.generate_signing_key("k1")
        self.fake.route("GET", USERINFO, self._userinfo)
        self.fake.route("POST", ONBIND, json_reply({}))
        self.fake.route("GET", KEYS, self._keys)

    def mxid(self, localpart: str) -> str:
        return f"@{localpart}:{self.server_name}"

    def _userinfo(self, req: Request) -> Reply:
        token = req.query.get("access_token", [""])[0]
        return json_reply({"sub": self.mxid(token)})

    def _keys(self, req: Request) -> Reply:
        key = encode_base64(self.signing_key.verify_key.encode())
        return json_reply(
            {
                "server_name": self.server_name,
                "verify_keys": {"ed25519:k1": {"key": key}},
                "valid_until_ts": 2**62,
            }
        )

    def x_matrix(self, method: str, uri: str, destination: str, content: Any) -> str:
        signed = signedjson.sign.sign_json(
            {
                "method": method,
                "uri": uri,
                "origin": self.server_name,
                "destination_is": destination,
                "content": content,
            },
            self.server_name,
            self.signing_key,
        )
        sig = signed["signatures"][self.server_name]["ed25519:k1"]
        return f'X-Matrix origin={self.server_name},key="ed25519:k1",sig="{sig}"'


def register(sydent: Sydent, hs: Homeserver, localpart: str) -> str:
    resp = sydent.request(
        "POST",
        f"{V2}/account/register",
        json_body={"matrix_server_name": hs.server_name, "access_token": localpart},
    )
    assert resp.status == 200, resp
    token: str = resp.json()["token"]
    return token


def auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_from_mail(mail: Mail) -> str:
    match = re.search(r"the code is (\S+)", mail.text)
    assert match, mail.text
    return match.group(1)


def link_from_mail(mail: Mail) -> str:
    match = re.search(
        r"(\S*/_matrix/identity/api/v1/validate/email/submitToken\S*)", mail.text
    )
    assert match, mail.text
    return match.group(1)


def request_email_token(
    sydent: Sydent,
    env: Env,
    address: str,
    secret: str,
    token: Optional[str] = None,
    send_attempt: int = 1,
    extra: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Mail]:
    """Request an email validation token; returns (sid, the mail that arrived)."""
    before = len(env.smtp.messages)
    body: Dict[str, Any] = {
        "email": address,
        "client_secret": secret,
        "send_attempt": send_attempt,
    }
    body.update(extra or {})
    path = (
        f"{V2}/validate/email/requestToken"
        if token
        else f"{V1}/validate/email/requestToken"
    )
    resp = sydent.request(
        "POST", path, json_body=body, headers=auth(token) if token else None
    )
    assert resp.status == 200, resp
    mail = env.smtp.wait_for(before + 1)[before]
    return resp.json()["sid"], mail


def validated_email_session(sydent: Sydent, env: Env, address: str, secret: str) -> str:
    sid, mail = request_email_token(sydent, env, address, secret)
    resp = sydent.request(
        "POST",
        f"{V1}/validate/email/submitToken",
        json_body={"sid": sid, "client_secret": secret, "token": token_from_mail(mail)},
    )
    assert resp.status == 200 and resp.json() == {"success": True}, resp
    return sid


def internal_bind(
    sydent: Sydent, medium: str, address: str, mxid: str
) -> Dict[str, Any]:

    resp = http_request(
        sydent.internal_url,
        "POST",
        "/_matrix/identity/internal/bind",
        json_body={"medium": medium, "address": address, "mxid": mxid},
    )
    assert resp.status == 200, resp
    result: Dict[str, Any] = resp.json()
    return result


def v1_lookup(sydent: Sydent, medium: str, address: str) -> Any:
    query = urllib.parse.urlencode({"medium": medium, "address": address})
    resp = sydent.request("GET", f"{V1}/lookup?{query}")
    assert resp.status == 200, resp
    return resp.json()


def pubkey(sydent: Sydent) -> str:
    resp = sydent.request("GET", f"{V2}/pubkey/ed25519:0")
    assert resp.status == 200, resp
    key: str = resp.json()["public_key"]
    return key


def verify_signed(obj: Dict[str, Any], signer: str, public_key: str) -> None:
    """Check `obj` carries a valid `ed25519:0` signature from `signer`.

    `public_key` is unpadded base64, in the standard or the URL-safe alphabet.
    """
    standard = public_key.translate(str.maketrans("-_", "+/"))
    key_bytes = base64.b64decode(standard + "=" * (-len(standard) % 4), validate=True)
    key = signedjson.key.decode_verify_key_bytes("ed25519:0", key_bytes)
    signedjson.sign.verify_signed_json(obj, signer, key)
