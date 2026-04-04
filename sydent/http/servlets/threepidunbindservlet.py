# Copyright 2018-2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
from http import HTTPStatus

import aiohttp
from aiohttp import web
from signedjson.sign import SignatureVerifyException

from sydent.db.valsession import ThreePidValSessionStore
from sydent.hs_federation.verifier import InvalidServerName, NoAuthenticationError
from sydent.http.servlets import json_response
from sydent.util import json_decoder
from sydent.util.stringutils import is_valid_client_secret
from sydent.validators import (
    IncorrectClientSecretException,
    InvalidSessionIdException,
    SessionNotValidatedException,
)

logger = logging.getLogger(__name__)


async def handle_threepid_unbind_post(request: web.Request) -> web.Response:
    sydent = request.app["sydent"]

    try:
        try:
            body_bytes = await request.read()
            body = json_decoder.decode(body_bytes.decode("UTF-8"))
        except ValueError:
            return json_response(
                {"errcode": "M_BAD_JSON", "error": "Malformed JSON"},
                status=HTTPStatus.BAD_REQUEST,
            )

        missing = [k for k in ("threepid", "mxid") if k not in body]
        if len(missing) > 0:
            msg = "Missing parameters: " + (",".join(missing))
            return json_response(
                {"errcode": "M_MISSING_PARAMS", "error": msg},
                status=HTTPStatus.BAD_REQUEST,
            )

        threepid = body["threepid"]
        mxid = body["mxid"]

        if "medium" not in threepid or "address" not in threepid:
            return json_response(
                {
                    "errcode": "M_MISSING_PARAMS",
                    "error": "Threepid lacks medium / address",
                },
                status=HTTPStatus.BAD_REQUEST,
            )

        # We now check for authentication in two different ways, depending
        # on the contents of the request. If the user has supplied "sid"
        # (the Session ID returned by Sydent during the original binding)
        # and "client_secret" fields, they are trying to prove that they
        # were the original author of the bind. We then check that what
        # they supply matches and if it does, allow the unbind.
        #
        # However if these fields are not supplied, we instead check
        # whether the request originated from a homeserver, and if so the
        # same homeserver that originally created the bind. We do this by
        # checking the signature of the request. If it all matches up, we
        # allow the unbind.
        #
        # Only one method of authentication is required.
        if "sid" in body and "client_secret" in body:
            sid = body["sid"]
            client_secret = body["client_secret"]

            if not is_valid_client_secret(client_secret):
                return json_response(
                    {
                        "errcode": "M_INVALID_PARAM",
                        "error": "Invalid client_secret provided",
                    },
                    status=HTTPStatus.BAD_REQUEST,
                )

            valSessionStore = ThreePidValSessionStore(sydent)

            try:
                s = valSessionStore.getValidatedSession(sid, client_secret)
            except (IncorrectClientSecretException, InvalidSessionIdException):
                return json_response(
                    {
                        "errcode": "M_NO_VALID_SESSION",
                        "error": "No valid session was found matching that sid and client secret",
                    },
                    status=HTTPStatus.UNAUTHORIZED,
                )
            except SessionNotValidatedException:
                return json_response(
                    {
                        "errcode": "M_SESSION_NOT_VALIDATED",
                        "error": "This validation session has not yet been completed",
                    },
                    status=HTTPStatus.FORBIDDEN,
                )

            if s.medium != threepid["medium"] or s.address != threepid["address"]:
                return json_response(
                    {
                        "errcode": "M_FORBIDDEN",
                        "error": "Provided session information does not match medium/address combo",
                    },
                    status=HTTPStatus.FORBIDDEN,
                )
        else:
            try:
                origin_server_name = await sydent.sig_verifier.authenticate_request(
                    request, body
                )
            except SignatureVerifyException as ex:
                return json_response(
                    {"errcode": "M_FORBIDDEN", "error": str(ex)},
                    status=HTTPStatus.UNAUTHORIZED,
                )
            except NoAuthenticationError as ex:
                return json_response(
                    {"errcode": "M_FORBIDDEN", "error": str(ex)},
                    status=HTTPStatus.UNAUTHORIZED,
                )
            except InvalidServerName as ex:
                return json_response(
                    {"errcode": "M_INVALID_PARAM", "error": str(ex)},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except (
                aiohttp.ClientConnectorError,
                aiohttp.ClientError,
                OSError,
            ) as e:
                msg = (
                    f"Unable to contact the Matrix homeserver to "
                    f"authenticate request ({type(e).__name__})"
                )
                logger.warning(msg)
                return json_response(
                    {
                        "errcode": "M_UNKNOWN",
                        "error": msg,
                    },
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            except Exception:
                logger.exception("Exception whilst authenticating unbind request")
                return json_response(
                    {"errcode": "M_UNKNOWN", "error": "Internal Server Error"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

            if not mxid.endswith(":" + origin_server_name):
                return json_response(
                    {
                        "errcode": "M_FORBIDDEN",
                        "error": "Origin server name does not match mxid",
                    },
                    status=HTTPStatus.FORBIDDEN,
                )

        sydent.threepidBinder.removeBinding(threepid, mxid)

        return json_response({})
    except Exception as ex:
        logger.exception("Exception whilst handling unbind")
        return json_response(
            {"errcode": "M_UNKNOWN", "error": str(ex)},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
