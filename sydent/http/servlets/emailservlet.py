# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.


from aiohttp import web

from sydent.http.auth import authV2
from sydent.http.servlets import get_args, json_response
from sydent.types import JsonDict
from sydent.util.emailutils import EmailAddressException, EmailSendException
from sydent.util.stringutils import MAX_EMAIL_ADDRESS_LENGTH, is_valid_client_secret
from sydent.validators import (
    IncorrectClientSecretException,
    IncorrectSessionTokenException,
    InvalidSessionIdException,
    SessionExpiredException,
)


async def handle_email_request_code_post(
    request: web.Request, require_auth: bool = False
) -> web.Response:
    sydent = request.app["sydent"]

    ipaddress = sydent.ip_from_request(request)

    if require_auth:
        account = await authV2(sydent, request)
        sydent.email_sender_ratelimiter.ratelimit(account.userId)
    elif ipaddress:
        sydent.email_sender_ratelimiter.ratelimit(ipaddress)

    args = await get_args(request, ("email", "client_secret", "send_attempt"))

    email = args["email"]
    clientSecret = args["client_secret"]

    try:
        sendAttempt = int(args["send_attempt"])
    except (TypeError, ValueError):
        return json_response(
            {
                "errcode": "M_INVALID_PARAM",
                "error": f"send_attempt should be an integer (got {args['send_attempt']}",
            },
            status=400,
        )

    if not is_valid_client_secret(clientSecret):
        return json_response(
            {
                "errcode": "M_INVALID_PARAM",
                "error": "Invalid client_secret provided",
            },
            status=400,
        )

    if not (0 < len(email) <= MAX_EMAIL_ADDRESS_LENGTH):
        return json_response(
            {"errcode": "M_INVALID_PARAM", "error": "Invalid email provided"},
            status=400,
        )

    brand = sydent.brand_from_request(request)

    nextLink: str | None = None
    if "next_link" in args and not args["next_link"].startswith("file:///"):
        nextLink = args["next_link"]

    try:
        sid = sydent.validators.email.requestToken(
            email,
            clientSecret,
            sendAttempt,
            nextLink,
            ipaddress=ipaddress,
            brand=brand,
        )
        resp = {"sid": str(sid)}
    except EmailAddressException:
        resp = {"errcode": "M_INVALID_EMAIL", "error": "Invalid email address"}
        return json_response(resp, status=400)
    except EmailSendException:
        resp = {"errcode": "M_EMAIL_SEND_ERROR", "error": "Failed to send email"}
        return json_response(resp, status=500)

    return json_response(resp)


async def handle_email_validate_code_get(
    request: web.Request, require_auth: bool = False
) -> web.Response:
    sydent = request.app["sydent"]
    args = await get_args(request, ("nextLink",), required=False)

    resp = None
    try:
        resp = await _do_email_validate_request(sydent, request)
    except Exception:
        pass

    if resp and "success" in resp and resp["success"]:
        msg = (
            "Verification successful! Please return to your Matrix client to continue."
        )
        if "nextLink" in args:
            next_link = args["nextLink"]
            if not next_link.startswith("file:///"):
                raise web.HTTPFound(location=next_link)
    else:
        msg = "Verification failed: you may need to request another verification email"

    brand = sydent.brand_from_request(request)

    # sydent.config.http.verify_response_template is deprecated
    if sydent.config.http.verify_response_template is None:
        templateFile = sydent.get_branded_template(
            brand,
            "verify_response_template.html",
        )
    else:
        templateFile = sydent.config.http.verify_response_template

    res = open(templateFile).read() % {"message": msg}

    return web.Response(text=res, content_type="text/html")


async def handle_email_validate_code_post(
    request: web.Request, require_auth: bool = False
) -> web.Response:
    sydent = request.app["sydent"]

    if require_auth:
        await authV2(sydent, request)

    result = await _do_email_validate_request(sydent, request)
    return json_response(result)


async def _do_email_validate_request(
    sydent: "object", request: web.Request
) -> JsonDict:
    """
    Extracts information about a validation session from the request and
    attempts to validate that session.

    :param sydent: The Sydent instance.
    :param request: The request to extract information about the session from.

    :return: A dict with a "success" key which value indicates whether the
        validation succeeded. If the validation failed, this dict also includes
        a "errcode" and a "error" keys which include information about the failure.
    """
    args = await get_args(request, ("token", "sid", "client_secret"))

    sid = args["sid"]
    tokenString = args["token"]
    clientSecret = args["client_secret"]

    if not is_valid_client_secret(clientSecret):
        return {
            "errcode": "M_INVALID_PARAM",
            "error": "Invalid client_secret provided",
        }

    try:
        return sydent.validators.email.validateSessionWithToken(
            sid, clientSecret, tokenString
        )
    except IncorrectClientSecretException:
        return {
            "success": False,
            "errcode": "M_INVALID_PARAM",
            "error": "Client secret does not match the one given when requesting the token",
        }
    except SessionExpiredException:
        return {
            "success": False,
            "errcode": "M_SESSION_EXPIRED",
            "error": "This validation session has expired: call requestToken again",
        }
    except InvalidSessionIdException:
        return {
            "success": False,
            "errcode": "M_INVALID_PARAM",
            "error": "The token doesn't match",
        }
    except IncorrectSessionTokenException:
        return {
            "success": False,
            "errcode": "M_NO_VALID_SESSION",
            "error": "No session could be found with this sid",
        }
