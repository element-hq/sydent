# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Where request parameters are read from on v1 and v2 endpoints."""

from __future__ import annotations

import json
import urllib.parse
from typing import Dict

import pytest

from tests.blackbox.environment import Env
from tests.blackbox.flows import V1, V2
from tests.blackbox.harness import Sydent

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
JSON = {"Content-Type": "application/json"}
SIGN_ARGS = {"private_key": "x", "token": "unknown", "mxid": "@a:b.test"}


@pytest.fixture(scope="module")
def sydent(menv: Env) -> Sydent:
    return menv.sydent()


# On sign-ed25519, 404 M_UNRECOGNIZED means that the three arguments were read
# and the token is unknown. A missing argument would give 400 M_MISSING_PARAMS.


@pytest.mark.parametrize(
    "body, content_type",
    [
        (urllib.parse.urlencode(SIGN_ARGS).encode(), FORM["Content-Type"]),
        (json.dumps(SIGN_ARGS).encode(), JSON["Content-Type"]),
    ],
    ids=["form", "json"],
)
def test_v1_post_body(sydent: Sydent, body: bytes, content_type: str) -> None:
    resp = sydent.request(
        "POST", f"{V1}/sign-ed25519", body=body, headers={"Content-Type": content_type}
    )
    assert resp.status == 404, resp
    assert resp.json()["errcode"] == "M_UNRECOGNIZED"


@pytest.mark.parametrize("headers", [FORM, JSON], ids=["form", "json"])
def test_v1_post_null_body_falls_back_to_query(
    sydent: Sydent, headers: Dict[str, str]
) -> None:
    query = urllib.parse.urlencode(SIGN_ARGS)
    resp = sydent.request(
        "POST", f"{V1}/sign-ed25519?{query}", body=b"null", headers=headers
    )
    assert resp.status == 404, resp
    assert resp.json()["errcode"] == "M_UNRECOGNIZED"


def test_v1_non_json_content_type_ignores_json_body(sydent: Sydent) -> None:
    resp = sydent.request(
        "POST",
        f"{V1}/sign-ed25519",
        body=b'{"private_key": "x", "token": "t", "mxid": "@a:b.test"}',
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status == 400, resp
    assert resp.json()["errcode"] == "M_MISSING_PARAMS"


def test_v1_form_body_values_are_strings(sydent: Sydent) -> None:
    resp = sydent.request(
        "POST", f"{V1}/bulk_lookup", body=b"threepids=x", headers=FORM
    )
    assert resp.status == 400, resp
    assert resp.json() == {
        "errcode": "M_INVALID_PARAM",
        "error": "threepids must be a list",
    }


def test_v1_duplicated_query_key_counts_as_missing(sydent: Sydent) -> None:
    resp = sydent.request(
        "GET", f"{V1}/lookup?medium=email&medium=msisdn&address=a@b.test"
    )
    assert resp.status == 400, resp
    assert resp.json() == {
        "errcode": "M_MISSING_PARAMS",
        "error": "Missing parameters: medium",
    }


def test_v1_invalid_utf8_query_value(sydent: Sydent) -> None:
    resp = sydent.request("GET", f"{V1}/lookup?medium=email&address=%FF%FE")
    assert resp.status == 400, resp
    assert resp.json()["errcode"] == "M_INVALID_PARAM"


def test_v1_missing_query_arg(sydent: Sydent) -> None:
    resp = sydent.request("GET", f"{V1}/lookup?medium=email")
    assert resp.status == 400
    assert resp.json() == {
        "errcode": "M_MISSING_PARAMS",
        "error": "Missing parameters: address",
    }


def test_v2_post_ignores_query_string(sydent: Sydent) -> None:
    query = urllib.parse.urlencode(
        {"matrix_server_name": "hs.test", "access_token": "a"}
    )
    resp = sydent.request("POST", f"{V2}/account/register?{query}", json_body={})
    assert resp.status == 400, resp
    assert resp.json()["errcode"] == "M_MISSING_PARAMS"


def test_v2_post_ignores_form_body(sydent: Sydent) -> None:
    resp = sydent.request(
        "POST",
        f"{V2}/account/register",
        body=b"matrix_server_name=hs.test&access_token=a",
        headers=FORM,
    )
    assert resp.status == 400, resp
    assert resp.json()["errcode"] == "M_BAD_JSON"
