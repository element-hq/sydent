# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
# Copyright 2018 New Vector Ltd
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import functools
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from sydent.http.servlets import (
    cors_middleware,
    json_response,
    matrix_error_middleware,
    metrics_middleware,
)
from sydent.http.servlets.accountservlet import handle_account_get
from sydent.http.servlets.authenticated_bind_threepid_servlet import (
    handle_authenticated_bind_threepid_post,
)
from sydent.http.servlets.authenticated_unbind_threepid_servlet import (
    handle_authenticated_unbind_threepid_post,
)
from sydent.http.servlets.blindlysignstuffservlet import (
    handle_blindly_sign_stuff_post,
)
from sydent.http.servlets.bulklookupservlet import handle_bulk_lookup_post
from sydent.http.servlets.emailservlet import (
    handle_email_request_code_post,
    handle_email_validate_code_get,
    handle_email_validate_code_post,
)
from sydent.http.servlets.getvalidated3pidservlet import (
    handle_get_validated_3pid_get,
)
from sydent.http.servlets.hashdetailsservlet import handle_hash_details_get
from sydent.http.servlets.logoutservlet import handle_logout_post
from sydent.http.servlets.lookupservlet import handle_lookup_get
from sydent.http.servlets.lookupv2servlet import handle_lookup_v2_post
from sydent.http.servlets.msisdnservlet import (
    handle_msisdn_request_code_post,
    handle_msisdn_validate_code_get,
    handle_msisdn_validate_code_post,
)
from sydent.http.servlets.pubkeyservlets import (
    handle_ed25519_get,
    handle_ephemeral_pubkey_is_valid_get,
    handle_pubkey_is_valid_get,
)
from sydent.http.servlets.registerservlet import handle_register_post
from sydent.http.servlets.replication import handle_replication_push_post
from sydent.http.servlets.store_invite_servlet import handle_store_invite_post
from sydent.http.servlets.termsservlet import handle_terms_get, handle_terms_post
from sydent.http.servlets.threepidbindservlet import handle_threepid_bind_post
from sydent.http.servlets.threepidunbindservlet import handle_threepid_unbind_post
from sydent.http.servlets.versions import handle_versions_get

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)


def _with_require_auth(handler: Any, require_auth: bool) -> Any:
    """Wrap a handler that takes require_auth as a second argument."""

    @functools.wraps(handler)
    async def wrapper(request: web.Request) -> web.Response:
        return await handler(request, require_auth=require_auth)

    return wrapper


def _with_lookup_pepper(handler: Any, lookup_pepper: str) -> Any:
    """Wrap a handler that takes lookup_pepper as a second argument."""

    @functools.wraps(handler)
    async def wrapper(request: web.Request) -> web.Response:
        return await handler(request, lookup_pepper=lookup_pepper)

    return wrapper


def _make_client_app(sydent: "Sydent", lookup_pepper: str) -> web.Application:
    """Build the client-facing aiohttp application with all routes."""
    from sydent.util.ratelimiter import Ratelimiter

    app = web.Application(
        middlewares=[
            metrics_middleware,
            cors_middleware,
            matrix_error_middleware,
        ],
        client_max_size=512 * 1024,  # 512 KiB, matching old SizeLimitingRequest
    )
    app["sydent"] = sydent

    # Pre-populate ratelimiters for MSISDN servlets (avoids modifying app during requests).
    if hasattr(sydent.config, "sms"):
        app["msisdn_ratelimiter"] = Ratelimiter[str](
            burst=sydent.config.sms.msisdn_ratelimit_burst,
            rate_hz=sydent.config.sms.msisdn_ratelimit_rate_hz,
        )
        app["country_ratelimiter"] = Ratelimiter[int](
            burst=sydent.config.sms.country_ratelimit_burst,
            rate_hz=sydent.config.sms.country_ratelimit_rate_hz,
        )

    # Root path handlers (used by CorsServlet in old Twisted code, and test pings)
    async def _empty_json(request: web.Request) -> web.Response:
        return json_response({})

    app.router.add_get("/_matrix/identity/api/v1", _empty_json)
    app.router.add_get("/_matrix/identity/v2", _empty_json)

    # Public key routes (shared between v1 and v2)
    for prefix in ("/_matrix/identity/api/v1", "/_matrix/identity/v2"):
        app.router.add_get(f"{prefix}/pubkey/ed25519:0", handle_ed25519_get)
        app.router.add_get(f"{prefix}/pubkey/isvalid", handle_pubkey_is_valid_get)
        app.router.add_get(
            f"{prefix}/pubkey/ephemeral/isvalid",
            handle_ephemeral_pubkey_is_valid_get,
        )

    # Versions (no auth, shared)
    app.router.add_get("/_matrix/identity/versions", handle_versions_get)

    # v1 routes (optional)
    if sydent.config.general.enable_v1_access:
        v1 = "/_matrix/identity/api/v1"

        app.router.add_get(f"{v1}/lookup", handle_lookup_get)
        app.router.add_post(f"{v1}/bulk_lookup", handle_bulk_lookup_post)

        # v1 email validation
        app.router.add_post(
            f"{v1}/validate/email/requestToken",
            _with_require_auth(handle_email_request_code_post, require_auth=False),
        )
        app.router.add_get(
            f"{v1}/validate/email/submitToken",
            _with_require_auth(handle_email_validate_code_get, require_auth=False),
        )
        app.router.add_post(
            f"{v1}/validate/email/submitToken",
            _with_require_auth(handle_email_validate_code_post, require_auth=False),
        )

        # v1 msisdn validation
        app.router.add_post(
            f"{v1}/validate/msisdn/requestToken",
            _with_require_auth(handle_msisdn_request_code_post, require_auth=False),
        )
        app.router.add_get(
            f"{v1}/validate/msisdn/submitToken",
            _with_require_auth(handle_msisdn_validate_code_get, require_auth=False),
        )
        app.router.add_post(
            f"{v1}/validate/msisdn/submitToken",
            _with_require_auth(handle_msisdn_validate_code_post, require_auth=False),
        )

        # v1 3pid
        app.router.add_get(
            f"{v1}/3pid/getValidated3pid",
            _with_require_auth(handle_get_validated_3pid_get, require_auth=False),
        )
        app.router.add_post(f"{v1}/3pid/unbind", handle_threepid_unbind_post)

        # v1 store-invite, sign
        app.router.add_post(
            f"{v1}/store-invite",
            _with_require_auth(handle_store_invite_post, require_auth=False),
        )
        app.router.add_post(
            f"{v1}/sign-ed25519",
            _with_require_auth(handle_blindly_sign_stuff_post, require_auth=False),
        )

    if sydent.config.general.enable_v1_associations:
        v1 = "/_matrix/identity/api/v1"
        app.router.add_post(
            f"{v1}/3pid/bind",
            _with_require_auth(handle_threepid_bind_post, require_auth=False),
        )

    # v2 routes
    v2 = "/_matrix/identity/v2"

    # v2 account
    app.router.add_get(f"{v2}/account", handle_account_get)
    app.router.add_post(f"{v2}/account/register", handle_register_post)
    app.router.add_post(f"{v2}/account/logout", handle_logout_post)

    # v2 terms
    app.router.add_get(f"{v2}/terms", handle_terms_get)
    app.router.add_post(f"{v2}/terms", handle_terms_post)

    # v2 email validation
    app.router.add_post(
        f"{v2}/validate/email/requestToken",
        _with_require_auth(handle_email_request_code_post, require_auth=True),
    )
    app.router.add_get(
        f"{v2}/validate/email/submitToken",
        _with_require_auth(handle_email_validate_code_get, require_auth=True),
    )
    app.router.add_post(
        f"{v2}/validate/email/submitToken",
        _with_require_auth(handle_email_validate_code_post, require_auth=True),
    )

    # v2 msisdn validation
    app.router.add_post(
        f"{v2}/validate/msisdn/requestToken",
        _with_require_auth(handle_msisdn_request_code_post, require_auth=True),
    )
    app.router.add_get(
        f"{v2}/validate/msisdn/submitToken",
        _with_require_auth(handle_msisdn_validate_code_get, require_auth=True),
    )
    app.router.add_post(
        f"{v2}/validate/msisdn/submitToken",
        _with_require_auth(handle_msisdn_validate_code_post, require_auth=True),
    )

    # v2 3pid
    app.router.add_get(
        f"{v2}/3pid/getValidated3pid",
        _with_require_auth(handle_get_validated_3pid_get, require_auth=True),
    )
    app.router.add_post(
        f"{v2}/3pid/bind",
        _with_require_auth(handle_threepid_bind_post, require_auth=True),
    )
    app.router.add_post(f"{v2}/3pid/unbind", handle_threepid_unbind_post)

    # v2 store-invite, sign, lookup, hash_details
    app.router.add_post(
        f"{v2}/store-invite",
        _with_require_auth(handle_store_invite_post, require_auth=True),
    )
    app.router.add_post(
        f"{v2}/sign-ed25519",
        _with_require_auth(handle_blindly_sign_stuff_post, require_auth=True),
    )
    app.router.add_post(
        f"{v2}/lookup",
        _with_lookup_pepper(handle_lookup_v2_post, lookup_pepper),
    )
    app.router.add_get(
        f"{v2}/hash_details",
        _with_lookup_pepper(handle_hash_details_get, lookup_pepper),
    )

    return app


def _make_internal_app(sydent: "Sydent") -> web.Application:
    """Build the internal API aiohttp application."""
    app = web.Application(
        middlewares=[
            matrix_error_middleware,
        ],
    )
    app["sydent"] = sydent

    app.router.add_post(
        "/_matrix/identity/internal/bind", handle_authenticated_bind_threepid_post
    )
    app.router.add_post(
        "/_matrix/identity/internal/unbind", handle_authenticated_unbind_threepid_post
    )

    return app


def _make_replication_app(sydent: "Sydent") -> web.Application:
    """Build the replication HTTPS aiohttp application."""
    app = web.Application(
        middlewares=[
            matrix_error_middleware,
        ],
    )
    app["sydent"] = sydent

    app.router.add_post(
        "/_matrix/identity/replicate/v1/push", handle_replication_push_post
    )

    return app


class ClientApiHttpServer:
    def __init__(self, sydent: "Sydent", lookup_pepper: str) -> None:
        self.sydent = sydent
        self.app = _make_client_app(sydent, lookup_pepper)

    async def start(self) -> web.AppRunner:
        runner = web.AppRunner(self.app)
        await runner.setup()

        port = self.sydent.config.http.client_port
        interface = self.sydent.config.http.client_bind_address

        logger.info("Starting Client API HTTP server on %s:%d", interface, port)
        site = web.TCPSite(runner, interface, port, backlog=50)
        await site.start()
        return runner


class InternalApiHttpServer:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent
        self.app = _make_internal_app(sydent)

    async def start(self, interface: str, port: int) -> web.AppRunner:
        runner = web.AppRunner(self.app)
        await runner.setup()

        logger.info("Starting Internal API HTTP server on %s:%d", interface, port)
        site = web.TCPSite(runner, interface, port, backlog=50)
        await site.start()
        return runner


class ReplicationHttpsServer:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent
        self.app = _make_replication_app(sydent)

    async def start(self) -> web.AppRunner | None:
        ssl_ctx = self.sydent.sslComponents.ssl_context
        if ssl_ctx is None:
            return None

        runner = web.AppRunner(self.app)
        await runner.setup()

        port = self.sydent.config.http.replication_port
        interface = self.sydent.config.http.replication_bind_address

        logger.info("Starting Replication HTTPS server on %s:%d", interface, port)
        site = web.TCPSite(runner, interface, port, backlog=50, ssl_context=ssl_ctx)
        await site.start()
        return runner
