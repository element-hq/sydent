# Copyright 2025 New Vector Ltd.
# Copyright 2017 Vector Creations Ltd
# Copyright 2016 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging

import phonenumbers
from aiohttp import web

from sydent.http.auth import authV2
from sydent.http.servlets import get_args, json_response
from sydent.types import JsonDict
from sydent.util.stringutils import is_valid_client_secret
from sydent.validators import (
    DestinationRejectedException,
    IncorrectClientSecretException,
    IncorrectSessionTokenException,
    InvalidSessionIdException,
    SessionExpiredException,
)

logger = logging.getLogger(__name__)


async def handle_msisdn_request_code_post(
    request: web.Request, require_auth: bool = False
) -> web.Response:
    sydent = request.app["sydent"]

    if require_auth:
        await authV2(sydent, request)

    args = await get_args(
        request, ("phone_number", "country", "client_secret", "send_attempt")
    )

    raw_phone_number = args["phone_number"]
    country = args["country"]
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
    clientSecret = args["client_secret"]

    if not is_valid_client_secret(clientSecret):
        return json_response(
            {
                "errcode": "M_INVALID_PARAM",
                "error": "Invalid client_secret provided",
            },
            status=400,
        )

    try:
        phone_number_object = phonenumbers.parse(raw_phone_number, country)

        if phone_number_object.country_code is None:
            raise Exception("No country code")
    except Exception as e:
        logger.warning("Invalid phone number given: %r", e)
        return json_response(
            {
                "errcode": "M_INVALID_PHONE_NUMBER",
                "error": "Invalid phone number",
            },
            status=400,
        )

    msisdn = phonenumbers.format_number(
        phone_number_object, phonenumbers.PhoneNumberFormat.E164
    )[1:]

    # Ratelimiters are pre-populated in the app dict by httpserver.py.

    request.app["msisdn_ratelimiter"].ratelimit(
        msisdn, "Limit exceeded for this number"
    )
    request.app["country_ratelimiter"].ratelimit(
        phone_number_object.country_code, "Limit exceeded for this country"
    )

    # International formatted number.
    intl_fmt = phonenumbers.format_number(
        phone_number_object, phonenumbers.PhoneNumberFormat.INTERNATIONAL
    )

    brand = sydent.brand_from_request(request)
    try:
        sid = await sydent.validators.msisdn.requestToken(
            phone_number_object, clientSecret, sendAttempt, brand
        )
        resp = {
            "success": True,
            "sid": str(sid),
            "msisdn": msisdn,
            "intl_fmt": intl_fmt,
        }
    except DestinationRejectedException:
        logger.warning("Destination rejected for number: %s", msisdn)
        return json_response(
            {
                "errcode": "M_DESTINATION_REJECTED",
                "error": "Phone numbers in this country are not currently supported",
            },
            status=400,
        )
    except Exception:
        logger.exception("Exception sending SMS")
        return json_response(
            {"errcode": "M_UNKNOWN", "error": "Internal Server Error"},
            status=500,
        )

    return json_response(resp)


async def handle_msisdn_validate_code_get(
    request: web.Request, require_auth: bool = False
) -> web.Response:
    sydent = request.app["sydent"]

    args = await get_args(request, ("token", "sid", "client_secret"))
    resp = await _do_msisdn_validate_request(sydent, request)
    if "success" in resp and resp["success"]:
        msg = (
            "Verification successful! Please return to your Matrix client to continue."
        )
        if "next_link" in args:
            next_link = args["next_link"]
            raise web.HTTPFound(location=next_link)
    else:
        msg = "Verification failed: you may need to request another verification text"

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


async def handle_msisdn_validate_code_post(
    request: web.Request, require_auth: bool = False
) -> web.Response:
    sydent = request.app["sydent"]

    if require_auth:
        await authV2(sydent, request)

    result = await _do_msisdn_validate_request(sydent, request)
    return json_response(result)


async def _do_msisdn_validate_request(
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
        return sydent.validators.msisdn.validateSessionWithToken(
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
