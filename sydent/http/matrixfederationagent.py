# Copyright 2019-2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
import random
import ssl
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import aiohttp
import attr
from netaddr import IPAddress

from sydent.http.federation_tls_options import ClientTLSOptionsFactory
from sydent.http.httpcommon import BodyExceededMaxSize
from sydent.http.srvresolver import SrvResolver, pick_server_from_list
from sydent.util import json_decoder
from sydent.util.ttlcache import TTLCache

# period to cache .well-known results for by default
WELL_KNOWN_DEFAULT_CACHE_PERIOD = 24 * 3600

# jitter to add to the .well-known default cache ttl
WELL_KNOWN_DEFAULT_CACHE_PERIOD_JITTER = 10 * 60

# period to cache failure to fetch .well-known for
WELL_KNOWN_INVALID_CACHE_PERIOD = 1 * 3600

# cap for .well-known cache period
WELL_KNOWN_MAX_CACHE_PERIOD = 48 * 3600

# The maximum size (in bytes) to allow a well-known file to be.
WELL_KNOWN_MAX_SIZE = 50 * 1024  # 50 KiB

logger = logging.getLogger(__name__)
well_known_cache: TTLCache[bytes, bytes | None] = TTLCache("well-known")


class MatrixFederationAgent:
    """An agent which provides a ``request`` method that will look up a Matrix
    server and send an HTTP request to it.

    Handles .well-known delegation, SRV resolution, and custom TLS options.

    :param session: An aiohttp.ClientSession to use for .well-known lookups.
        Federation requests are made with per-request TLS settings so this
        session is also used for them (with explicit ``ssl`` kwarg).

    :param tls_client_options_factory: Factory to use for fetching client TLS
        options, or None to disable TLS verification.

    :param srv_resolver: SRV resolver instance.

    :param well_known_cache_: TTLCache for storing cached well-known lookups.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        tls_client_options_factory: ClientTLSOptionsFactory | None,
        srv_resolver: SrvResolver | None = None,
        well_known_cache_: TTLCache[bytes, bytes | None] = well_known_cache,
    ) -> None:
        self._session = session
        self._tls_client_options_factory = tls_client_options_factory

        if srv_resolver is None:
            srv_resolver = SrvResolver()
        self._srv_resolver = srv_resolver

        self._well_known_cache = well_known_cache_

    async def request(
        self,
        method: str,
        uri: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> aiohttp.ClientResponse:
        """Make an HTTP request to a Matrix federation endpoint.

        :param method: HTTP method (e.g. "GET", "POST").
        :param uri: Absolute URI to be retrieved.
        :param headers: HTTP headers to send with the request.
        :param body: Request body bytes, or None.

        :returns the response object. The caller is responsible for reading
            and closing it.
        """
        parsed = urlparse(uri)
        # Determine host and port from the URI
        host = (parsed.hostname or "").encode("ascii")
        port = parsed.port if parsed.port is not None else -1
        path = parsed.path or "/"
        if parsed.query:
            path = path + "?" + parsed.query

        routing = await self._route_matrix_uri(host, port, path.encode("ascii"))

        # Build actual URL with resolved host:port
        target_host = routing.target_host.decode("ascii")
        actual_url = f"https://{target_host}:{routing.target_port}{path}"

        if headers is None:
            headers = {}

        if "Host" not in headers:
            headers["Host"] = routing.host_header.decode("ascii")

        # Determine TLS settings
        ssl_ctx: ssl.SSLContext | bool
        if self._tls_client_options_factory is None:
            ssl_ctx = False
        else:
            ssl_ctx = self._tls_client_options_factory.get_ssl_context(
                routing.tls_server_name.decode("ascii")
            )

        logger.info(
            "Connecting to %s:%d for federation request",
            target_host,
            routing.target_port,
        )

        resp = await self._session.request(
            method,
            actual_url,
            headers=headers,
            data=body,
            ssl=ssl_ctx,
        )
        return resp

    async def _route_matrix_uri(
        self,
        host: bytes,
        port: int,
        path: bytes,
        lookup_well_known: bool = True,
    ) -> "_RoutingResult":
        """Determine the routing for a Matrix URI.

        :param host: The hostname from the URI.
        :param port: The port from the URI, or -1 if none given.
        :param path: The path component of the URI.
        :param lookup_well_known: Whether to try .well-known delegation.

        :returns a routing result.
        """
        # Check for an IP literal
        try:
            ip_address = IPAddress(host.decode("ascii"))
        except Exception:
            ip_address = None

        if ip_address:
            if port == -1:
                port = 8448
            netloc = host if port == 8448 else host + b":" + str(port).encode()
            return _RoutingResult(
                host_header=netloc,
                tls_server_name=host,
                target_host=host,
                target_port=port,
            )

        if port != -1:
            # There is an explicit port
            netloc = host + b":" + str(port).encode()
            return _RoutingResult(
                host_header=netloc,
                tls_server_name=host,
                target_host=host,
                target_port=port,
            )

        if lookup_well_known:
            well_known_server = await self._get_well_known(host)

            if well_known_server:
                # Parse the server name in the .well-known response into host/port.
                if b":" in well_known_server:
                    wk_host, wk_port_raw = well_known_server.rsplit(b":", 1)
                    try:
                        wk_port = int(wk_port_raw)
                    except ValueError:
                        wk_host, wk_port = well_known_server, -1
                else:
                    wk_host, wk_port = well_known_server, -1

                res = await self._route_matrix_uri(
                    wk_host, wk_port, path, lookup_well_known=False
                )
                return res

        # Look up SRV for Matrix 1.8 `matrix-fed` service first
        service_name = b"_matrix-fed._tcp.%s" % (host,)
        server_list = await self._srv_resolver.resolve_service(service_name)
        if server_list:
            target_host, target_port = pick_server_from_list(server_list)
            logger.debug(
                "Picked %s:%i from _matrix-fed SRV records for %s",
                target_host.decode("ascii"),
                target_port,
                host.decode("ascii"),
            )
        else:
            # Fall back to deprecated `matrix` service
            service_name = b"_matrix._tcp.%s" % (host,)
            server_list = await self._srv_resolver.resolve_service(service_name)

            if not server_list:
                target_host = host
                target_port = 8448
                logger.debug(
                    "No SRV record for %s, using %s:%i",
                    host.decode("ascii"),
                    target_host.decode("ascii"),
                    target_port,
                )
            else:
                target_host, target_port = pick_server_from_list(server_list)
                logger.debug(
                    "Picked %s:%i from _matrix SRV records for %s",
                    target_host.decode("ascii"),
                    target_port,
                    host.decode("ascii"),
                )

        return _RoutingResult(
            host_header=host,
            tls_server_name=host,
            target_host=target_host,
            target_port=target_port,
        )

    async def _get_well_known(self, server_name: bytes) -> bytes | None:
        """Attempt to fetch and parse a .well-known file for the given server.

        :param server_name: Name of the server, from the requested url.

        :returns either the new server name, from the .well-known, or None if
            there was no .well-known file.
        """
        try:
            result = self._well_known_cache[server_name]
        except KeyError:
            result, cache_period = await self._do_get_well_known(server_name)

            if cache_period > 0:
                self._well_known_cache.set(server_name, result, cache_period)

        return result

    async def _do_get_well_known(
        self, server_name: bytes
    ) -> tuple[bytes | None, float]:
        """Actually fetch and parse a .well-known, without checking the cache.

        :param server_name: Name of the server, from the requested url.

        :returns a tuple of (result, cache period), where result is one of:
            - the new server name from the .well-known (as ``bytes``)
            - None if there was no .well-known file.
        """
        uri = "https://{}/.well-known/matrix/server".format(server_name.decode("ascii"))
        logger.info("Fetching %s", uri)
        cache_period: float | None
        try:
            async with self._session.get(uri) as response:
                body = await response.read()
                if len(body) > WELL_KNOWN_MAX_SIZE:
                    raise BodyExceededMaxSize()
                if response.status != 200:
                    raise Exception(f"Non-200 response {response.status}")

                parsed_body = json_decoder.decode(body.decode("utf-8"))
                logger.info("Response from .well-known: %s", parsed_body)
                if not isinstance(parsed_body, dict):
                    raise Exception("not a dict")
                if "m.server" not in parsed_body:
                    raise Exception("Missing key 'm.server'")
                if not isinstance(parsed_body["m.server"], str):
                    raise TypeError("m.server must be a string")

                result_value: bytes = parsed_body["m.server"].encode("ascii")

                cache_period = _cache_period_from_headers(response.headers)
                if cache_period is None:
                    cache_period = WELL_KNOWN_DEFAULT_CACHE_PERIOD
                    cache_period += random.uniform(
                        0, WELL_KNOWN_DEFAULT_CACHE_PERIOD_JITTER
                    )
                else:
                    cache_period = min(cache_period, WELL_KNOWN_MAX_CACHE_PERIOD)

                return (result_value, cache_period)
        except Exception as e:
            logger.info("Error fetching %s: %s", uri, e)

            cache_period = WELL_KNOWN_INVALID_CACHE_PERIOD
            cache_period += random.uniform(0, WELL_KNOWN_DEFAULT_CACHE_PERIOD_JITTER)
            return (None, cache_period)


def _cache_period_from_headers(
    headers: Any, time_now: Callable[[], float] = time.time
) -> float | None:
    """Extract a cache period from HTTP response headers.

    Checks Cache-Control and Expires headers.
    """
    cache_controls = _parse_cache_control(headers)

    if b"no-store" in cache_controls:
        return 0

    max_age = cache_controls.get(b"max-age")
    if max_age is not None:
        try:
            return int(max_age)
        except ValueError:
            pass

    expires_header = headers.get("Expires")
    if expires_header is not None:
        try:
            expires_date = parsedate_to_datetime(expires_header)
            return expires_date.timestamp() - time_now()
        except (ValueError, TypeError):
            # RFC7234 says 'A cache recipient MUST interpret invalid date formats,
            # especially the value "0", as representing a time in the past (i.e.,
            # "already expired").
            return 0

    return None


def _parse_cache_control(headers: Any) -> dict[bytes, bytes | None]:
    """Parse Cache-Control headers from an aiohttp response."""
    cache_controls: dict[bytes, bytes | None] = {}
    cc_header = headers.get("Cache-Control")
    if cc_header is None:
        return cache_controls
    for directive in cc_header.encode("ascii").split(b","):
        splits = [x.strip() for x in directive.split(b"=", 1)]
        k = splits[0].lower()
        v = splits[1] if len(splits) > 1 else None
        cache_controls[k] = v
    return cache_controls


@attr.s(frozen=True, slots=True, auto_attribs=True)
class _RoutingResult:
    """The result returned by ``_route_matrix_uri``.

    Contains the parameters needed to direct a federation connection to a particular
    server.

    Where a SRV record points to several servers, this object contains a single server
    chosen from the list.
    """

    host_header: bytes
    """
    The value we should assign to the Host header (host:port from the matrix
    URI, or .well-known).
    """

    tls_server_name: bytes
    """
    The server name we should set in the SNI (typically host, without port, from the
    matrix URI or .well-known)
    """

    target_host: bytes
    """
    The hostname (or IP literal) we should route the TCP connection to (the target of the
    SRV record, or the hostname from the URL/.well-known)
    """

    target_port: int
    """
    The port we should route the TCP connection to (the target of the SRV record, or
    the port from the URL/.well-known, or 8448)
    """
