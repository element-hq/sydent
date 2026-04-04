# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import json
import logging
from collections.abc import Iterable
from typing import Any

from aiohttp import web
from prometheus_client import Counter

from sydent.types import JsonDict
from sydent.util import json_decoder

logger = logging.getLogger(__name__)


request_counter = Counter(
    "sydent_http_received_requests",
    "Received requests",
    labelnames=("servlet", "method"),
)


class MatrixRestError(Exception):
    """
    Handled by the error-handling middleware. Any handlers that don't use
    the middleware should catch this exception themselves.
    """

    def __init__(self, httpStatus: int, errcode: str, error: str):
        super(Exception, self).__init__(error)
        self.httpStatus = httpStatus
        self.errcode = errcode
        self.error = error


def json_response(content: JsonDict, status: int = 200) -> web.Response:
    """Create a JSON response from a dict."""
    return web.Response(
        body=json.dumps(content).encode("UTF-8"),
        status=status,
        content_type="application/json",
    )


async def get_args(
    request: web.Request, args: Iterable[str], required: bool = True
) -> dict[str, Any]:
    """
    Helper function to get arguments for an HTTP request.
    Currently takes args from the top level keys of a json object or
    www-form-urlencoded for backwards compatibility on v1 endpoints only.

    :param request: The request received by the handler.
    :param args: The args to look for in the request's parameters.
    :param required: Whether to raise a MatrixRestError with 400
        M_MISSING_PARAMS if an argument is not found.

    :raises: MatrixRestError if required is True and a given parameter
        was not found in the request's query parameters.
    :raises: MatrixRestError if the request body contains bad JSON.
    :raises: MatrixRestError if arguments are given in www-form-urlencoded
        form, and some argument name or value is not a valid UTF-8-encoded
        string.

    :return: A dict containing the requested args and their values. String values
        are of type unicode.
    """
    v1_path = request.path.startswith("/_matrix/identity/api/v1")

    request_args = None
    # for v1 paths, only look for json args if content type is json
    if request.method in ("POST", "PUT") and (
        not v1_path
        or (
            request.content_type is not None
            and request.content_type.startswith("application/json")
        )
    ):
        try:
            body = await request.read()
            request_args = json_decoder.decode(body.decode("UTF-8"))
        except ValueError:
            raise MatrixRestError(400, "M_BAD_JSON", "Malformed JSON") from None

    # If we didn't get anything from that, and it's a v1 api path, try the request args
    if request_args is None and (v1_path or request.method == "GET"):
        request_args = {}
        for k, v in request.query.items():
            request_args[k] = v

    elif request_args is None:
        request_args = {}

    if required:
        # Check for any missing arguments
        missing = []
        for a in args:
            if a not in request_args:
                missing.append(a)

        if len(missing) > 0:
            msg = "Missing parameters: " + (",".join(missing))
            raise MatrixRestError(400, "M_MISSING_PARAMS", msg)

    return request_args


def dict_to_json_bytes(content: JsonDict) -> bytes:
    """
    Converts a dict into JSON and encodes it to bytes.

    :return: The JSON bytes.
    """
    return json.dumps(content).encode("UTF-8")


def send_cors(response: web.StreamResponse) -> None:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"


@web.middleware
async def matrix_error_middleware(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    """Middleware that catches MatrixRestError and returns JSON error responses."""
    try:
        response: web.StreamResponse = await handler(request)
        return response
    except MatrixRestError as e:
        return json_response(
            {"errcode": e.errcode, "error": e.error},
            status=e.httpStatus,
        )
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Exception processing request")
        return json_response(
            {"errcode": "M_UNKNOWN", "error": "Internal Server Error"},
            status=500,
        )


@web.middleware
async def cors_middleware(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    """Middleware that adds CORS headers to all responses and handles OPTIONS."""
    if request.method == "OPTIONS":
        resp = web.Response()
        send_cors(resp)
        return resp

    response: web.StreamResponse = await handler(request)
    send_cors(response)
    return response


@web.middleware
async def metrics_middleware(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    """Middleware that counts requests per handler."""
    name = getattr(handler, "__name__", handler.__class__.__name__)
    request_counter.labels(name, request.method).inc()
    response: web.StreamResponse = await handler(request)
    return response
