# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
import urllib
from http import HTTPStatus
from json import JSONDecodeError

import aiohttp
from aiohttp import web

from sydent.http.httpclient import FederationHttpClient
from sydent.http.servlets import get_args, json_response
from sydent.users.tokens import issueToken
from sydent.util.stringutils import is_valid_matrix_server_name

logger = logging.getLogger(__name__)


async def handle_register_post(request: web.Request) -> web.Response:
    """
    Register with the Identity Server
    """
    sydent = request.app["sydent"]
    client = FederationHttpClient(sydent)

    args = await get_args(request, ("matrix_server_name", "access_token"))

    matrix_server = args["matrix_server_name"].lower()

    if sydent.config.general.homeserver_allow_list:
        if matrix_server not in sydent.config.general.homeserver_allow_list:
            return json_response(
                {
                    "errcode": "M_UNAUTHORIZED",
                    "error": "This homeserver is not authorized to access this server.",
                },
                status=403,
            )

    if not is_valid_matrix_server_name(matrix_server):
        return json_response(
            {
                "errcode": "M_INVALID_PARAM",
                "error": "matrix_server_name must be a valid Matrix server name (IP address or hostname)",
            },
            status=400,
        )

    def federation_request_problem(error: str) -> web.Response:
        logger.warning(error)
        return json_response(
            {
                "errcode": "M_UNKNOWN",
                "error": error,
            },
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    try:
        result = await client.get_json(
            "matrix://{}/_matrix/federation/v1/openid/userinfo?access_token={}".format(
                matrix_server,
                urllib.parse.quote(args["access_token"]),
            ),
            1024 * 5,
        )
    except (aiohttp.ClientConnectorError, aiohttp.ClientError, OSError) as e:
        return federation_request_problem(
            f"Unable to contact the Matrix homeserver ({type(e).__name__})"
        )
    except JSONDecodeError:
        return federation_request_problem("The Matrix homeserver returned invalid JSON")

    if "sub" not in result:
        return federation_request_problem(
            "The Matrix homeserver did not include 'sub' in its response",
        )

    user_id = result["sub"]

    if not isinstance(user_id, str):
        return federation_request_problem(
            "The Matrix homeserver returned a malformed reply"
        )

    user_id_components = user_id.split(":", 1)

    # Ensure there's a localpart and domain in the returned user ID.
    if len(user_id_components) != 2:
        return federation_request_problem(
            "The Matrix homeserver returned an invalid MXID"
        )

    user_id_server = user_id_components[1]

    if not is_valid_matrix_server_name(user_id_server):
        return federation_request_problem(
            "The Matrix homeserver returned an invalid MXID"
        )

    if user_id_server != matrix_server:
        return federation_request_problem(
            "The Matrix homeserver returned a MXID belonging to another homeserver"
        )

    tok = issueToken(sydent, user_id)

    # XXX: `token` is correct for the spec, but we released with `access_token`
    # for a substantial amount of time. Serve both to make spec-compliant clients
    # happy.
    return json_response(
        {
            "access_token": tok,
            "token": tok,
        }
    )
