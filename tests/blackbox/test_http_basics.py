# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Routing, error bodies, CORS, HTTP methods and request size limits."""

from __future__ import annotations

import urllib.parse

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.flows import CORS, V1, V2, cors_headers
from tests.blackbox.harness import Sydent

# MAX_REQUEST_SIZE in sydent/http/httpcommon.py.
MAX_REQUEST_SIZE = 512 * 1024


@pytest.fixture(scope="module")
def sydent(menv: Env) -> Sydent:
    return menv.sydent()


def test_versions(sydent: Sydent) -> None:
    resp = sydent.request("GET", "/_matrix/identity/versions")
    assert resp.status == 200
    assert resp.header("Content-Type") == "application/json"
    assert resp.json() == {
        "versions": [
            "r0.1.0",
            "r0.2.0",
            "r0.2.1",
            "r0.3.0",
            "v1.1",
            "v1.2",
            "v1.3",
            "v1.4",
            "v1.5",
        ]
    }
    assert cors_headers(resp.headers) == CORS


@pytest.mark.parametrize("path", [V1, V2, "/_matrix/identity/versions", f"{V2}/lookup"])
def test_options_preflight(sydent: Sydent, path: str) -> None:
    resp = sydent.request("OPTIONS", path)
    assert resp.status == 200
    assert resp.body == b""
    assert cors_headers(resp.headers) == CORS


@pytest.mark.parametrize("path", [V1, V2])
def test_api_roots(sydent: Sydent, path: str) -> None:
    resp = sydent.request("GET", path)
    assert resp.status == 200
    assert resp.json() == {}
    assert cors_headers(resp.headers) == CORS


def test_cors_on_error_response(sydent: Sydent) -> None:
    resp = sydent.request("GET", f"{V2}/account")
    assert resp.status == 401
    assert resp.json() == {"errcode": "M_UNAUTHORIZED", "error": "Unauthorized"}
    assert cors_headers(resp.headers) == CORS


@pytest.mark.parametrize("method", ["GET", "OPTIONS"])
def test_unknown_path_is_404_without_cors(sydent: Sydent, method: str) -> None:
    resp = sydent.request(method, "/_matrix/identity/nope")
    assert resp.status == 404
    assert cors_headers(resp.headers) == {}


def test_unsupported_method_is_501(sydent: Sydent) -> None:
    # Existing behaviour: 501 rather than 405.
    resp = sydent.request("DELETE", f"{V2}/lookup")
    assert resp.status == 501


def test_head_on_get_endpoint(sydent: Sydent) -> None:
    resp = sydent.request("HEAD", "/_matrix/identity/versions")
    assert resp.status == 200
    assert resp.body == b""


def test_leaf_endpoints_answer_below_their_path(sydent: Sydent) -> None:
    # Existing behaviour: leaf endpoints also answer below their path.
    assert sydent.request("GET", f"{V2}/hash_details/").status == 401
    resp = sydent.request("GET", f"{V1}/lookup/foo?medium=email&address=a@b.test")
    assert resp.status == 200
    assert resp.json() == {}


@pytest.mark.parametrize("version", [V1, V2])
def test_pubkey(sydent: Sydent, version: str) -> None:
    resp = sydent.request("GET", f"{version}/pubkey/ed25519:0")
    assert resp.status == 200
    key = resp.json()["public_key"]
    assert isinstance(key, str) and len(key) == 43
    # Existing behaviour: the pubkey endpoints send no CORS headers.
    assert cors_headers(resp.headers) == {}

    query = urllib.parse.urlencode({"public_key": key})
    valid = sydent.request("GET", f"{version}/pubkey/isvalid?{query}")
    assert valid.status == 200
    assert valid.json() == {"valid": True}
    invalid = sydent.request("GET", f"{version}/pubkey/isvalid?public_key=nope")
    assert invalid.json() == {"valid": False}
    ephemeral = sydent.request("GET", f"{version}/pubkey/ephemeral/isvalid?{query}")
    assert ephemeral.status == 200
    assert ephemeral.json() == {"valid": False}


def test_pubkey_isvalid_missing_param(sydent: Sydent) -> None:
    resp = sydent.request("GET", f"{V2}/pubkey/isvalid")
    assert resp.status == 400
    assert resp.json()["errcode"] == "M_MISSING_PARAMS"


def test_pubkey_is_stable_across_versions(sydent: Sydent) -> None:
    v1 = sydent.request("GET", f"{V1}/pubkey/ed25519:0").json()
    v2 = sydent.request("GET", f"{V2}/pubkey/ed25519:0").json()
    assert v1 == v2


@pytest.mark.parametrize(
    "body, status, errcode",
    [
        (b"{not json", 400, "M_BAD_JSON"),
        (b'{"matrix_server_name": "\xff\xfe"}', 400, "M_BAD_JSON"),
        (b"[]", 400, "M_MISSING_PARAMS"),
        (b"{}", 400, "M_MISSING_PARAMS"),
        # Existing behaviour: a JSON scalar body is an unhandled error.
        (b"1", 500, "M_UNKNOWN"),
    ],
)
def test_bad_json_body(sydent: Sydent, body: bytes, status: int, errcode: str) -> None:
    resp = sydent.request(
        "POST",
        f"{V2}/account/register",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == status, resp
    assert resp.json()["errcode"] == errcode
    assert set(resp.json()) == {"errcode", "error"}
    assert cors_headers(resp.headers) == CORS


def test_missing_params_error_lists_them(sydent: Sydent) -> None:
    resp = sydent.request("POST", f"{V2}/account/register", json_body={})
    assert resp.status == 400
    assert resp.json() == {
        "errcode": "M_MISSING_PARAMS",
        "error": "Missing parameters: matrix_server_name,access_token",
    }


def test_unhandled_exception_is_500_json(sydent: Sydent) -> None:
    resp = sydent.request("POST", f"{V1}/bulk_lookup", json_body={"threepids": [1]})
    assert resp.status == 500
    assert resp.header("Content-Type") == "application/json"
    assert resp.json() == {"errcode": "M_UNKNOWN", "error": "Internal Server Error"}


def test_request_body_at_limit_is_processed(sydent: Sydent) -> None:
    body = b"{" + b" " * (MAX_REQUEST_SIZE - 2) + b"}"
    resp = sydent.request(
        "POST",
        f"{V2}/account/register",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    assert resp.json()["errcode"] == "M_MISSING_PARAMS"


def test_request_body_over_limit_is_refused(sydent: Sydent) -> None:
    body = b"{" + b" " * (MAX_REQUEST_SIZE - 1) + b"}"
    # Dropping the connection and answering 413 are both a refusal.
    try:
        resp = sydent.request(
            "POST",
            f"{V2}/account/register",
            body=body,
            headers={"Content-Type": "application/json"},
        )
    except OSError:
        return
    assert resp.status == 413, resp
