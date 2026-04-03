# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.

"""Tests for MatrixFederationAgent server discovery (.well-known, SRV, routing)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from twisted.internet import defer
from twisted.trial import unittest
from twisted.web.client import URI
from twisted.web.http_headers import Headers

from sydent.http.matrixfederationagent import (
    WELL_KNOWN_INVALID_CACHE_PERIOD,
    WELL_KNOWN_MAX_CACHE_PERIOD,
    MatrixFederationAgent,
    _cache_period_from_headers,
    _parse_cache_control,
)
from sydent.http.srvresolver import Server, SrvResolver
from sydent.util.ttlcache import TTLCache

from tests.utils import ResolvingMemoryReactorClock


def _make_well_known_response(
    body: bytes | None = None,
    status: int = 200,
    headers: dict[bytes, list[bytes]] | None = None,
) -> MagicMock:
    """Build a fake IResponse for .well-known fetches."""
    response = MagicMock()
    response.code = status
    response.headers = Headers()
    if headers:
        for k, vs in headers.items():
            for v in vs:
                response.headers.addRawHeader(k, v)
    return response


class ParseCacheControlTest(unittest.TestCase):
    """Tests for _parse_cache_control()."""

    def test_max_age(self) -> None:
        headers = Headers()
        headers.addRawHeader(b"cache-control", b"max-age=3600")
        result = _parse_cache_control(headers)
        self.assertEqual(result[b"max-age"], b"3600")

    def test_multiple_directives(self) -> None:
        headers = Headers()
        headers.addRawHeader(b"cache-control", b"no-store, max-age=300")
        result = _parse_cache_control(headers)
        self.assertIn(b"no-store", result)
        self.assertEqual(result[b"max-age"], b"300")

    def test_no_value_directive(self) -> None:
        headers = Headers()
        headers.addRawHeader(b"cache-control", b"no-cache")
        result = _parse_cache_control(headers)
        self.assertIn(b"no-cache", result)
        self.assertIsNone(result[b"no-cache"])

    def test_empty_headers(self) -> None:
        headers = Headers()
        result = _parse_cache_control(headers)
        self.assertEqual(result, {})


class CachePeriodFromHeadersTest(unittest.TestCase):
    """Tests for _cache_period_from_headers()."""

    def test_max_age(self) -> None:
        headers = Headers()
        headers.addRawHeader(b"cache-control", b"max-age=7200")
        period = _cache_period_from_headers(headers)
        self.assertEqual(period, 7200)

    def test_no_store(self) -> None:
        headers = Headers()
        headers.addRawHeader(b"cache-control", b"no-store")
        period = _cache_period_from_headers(headers)
        self.assertEqual(period, 0)

    def test_expires_header(self) -> None:
        headers = Headers()
        # Use an Expires header in HTTP date format.
        headers.addRawHeader(b"expires", b"Thu, 01 Jan 2099 00:00:00 GMT")
        period = _cache_period_from_headers(headers, time_now=lambda: 0)
        self.assertIsNotNone(period)
        assert period is not None
        self.assertGreater(period, 0)

    def test_invalid_expires(self) -> None:
        """Invalid Expires values should return 0 (already expired)."""
        headers = Headers()
        headers.addRawHeader(b"expires", b"0")
        period = _cache_period_from_headers(headers, time_now=lambda: 1000)
        self.assertEqual(period, 0)

    def test_no_cache_headers(self) -> None:
        headers = Headers()
        period = _cache_period_from_headers(headers)
        self.assertIsNone(period)

    def test_max_age_takes_precedence_over_expires(self) -> None:
        headers = Headers()
        headers.addRawHeader(b"cache-control", b"max-age=100")
        headers.addRawHeader(b"expires", b"Thu, 01 Jan 2099 00:00:00 GMT")
        period = _cache_period_from_headers(headers)
        self.assertEqual(period, 100)


class RouteMatrixUriTest(unittest.TestCase):
    """Tests for MatrixFederationAgent._route_matrix_uri() discovery chain."""

    def setUp(self) -> None:
        self.reactor = ResolvingMemoryReactorClock()
        self.mock_srv_resolver = MagicMock(spec=SrvResolver)
        self.mock_srv_resolver.resolve_service = AsyncMock(return_value=[])
        self.well_known_cache: TTLCache[bytes, bytes | None] = TTLCache("test-wk")

        self.agent = MatrixFederationAgent(
            reactor=self.reactor,
            tls_client_options_factory=None,
            _srv_resolver=self.mock_srv_resolver,
            _well_known_cache=self.well_known_cache,
        )

    async def test_ip_literal_default_port(self) -> None:
        """An IP literal with no port defaults to 8448."""
        uri = URI.fromBytes(b"matrix-federation://1.2.3.4/some/path", defaultPort=-1)
        result = await self.agent._route_matrix_uri(uri)
        self.assertEqual(result.target_host, b"1.2.3.4")
        self.assertEqual(result.target_port, 8448)
        self.assertEqual(result.tls_server_name, b"1.2.3.4")

    async def test_ip_literal_explicit_port(self) -> None:
        """An IP literal with an explicit port uses that port."""
        uri = URI.fromBytes(b"matrix-federation://1.2.3.4:9999/path", defaultPort=-1)
        result = await self.agent._route_matrix_uri(uri)
        self.assertEqual(result.target_host, b"1.2.3.4")
        self.assertEqual(result.target_port, 9999)

    async def test_explicit_port_skips_well_known_and_srv(self) -> None:
        """A hostname with an explicit port skips .well-known and SRV lookups."""
        uri = URI.fromBytes(
            b"matrix-federation://example.com:8449/path", defaultPort=-1
        )
        result = await self.agent._route_matrix_uri(uri)
        self.assertEqual(result.target_host, b"example.com")
        self.assertEqual(result.target_port, 8449)
        self.assertEqual(result.host_header, b"example.com:8449")
        # No SRV lookups should have occurred.
        self.mock_srv_resolver.resolve_service.assert_not_awaited()

    async def test_well_known_delegates(self) -> None:
        """A valid .well-known response redirects to the delegated server."""
        # Pre-populate the .well-known cache to avoid needing to mock HTTP.
        self.well_known_cache.set(b"example.com", b"delegated.example.com:8448", 3600)

        uri = URI.fromBytes(b"matrix-federation://example.com/path", defaultPort=-1)
        result = await self.agent._route_matrix_uri(uri)
        self.assertEqual(result.target_host, b"delegated.example.com")
        self.assertEqual(result.target_port, 8448)
        self.assertEqual(result.host_header, b"delegated.example.com:8448")
        self.assertEqual(result.tls_server_name, b"delegated.example.com")

    async def test_well_known_delegates_no_port_falls_to_srv(self) -> None:
        """A .well-known with no port triggers SRV lookup on delegated host."""
        self.well_known_cache.set(b"example.com", b"delegated.example.com", 3600)

        self.mock_srv_resolver.resolve_service.return_value = [
            Server(
                host=b"srv.delegated.example.com",
                port=443,
                priority=1,
                weight=1,
                expires=9999,
            )
        ]

        uri = URI.fromBytes(b"matrix-federation://example.com/path", defaultPort=-1)
        result = await self.agent._route_matrix_uri(uri)
        self.assertEqual(result.target_host, b"srv.delegated.example.com")
        self.assertEqual(result.target_port, 443)

    async def test_well_known_none_falls_to_srv(self) -> None:
        """When .well-known returns None, fall through to SRV."""
        self.well_known_cache.set(b"example.com", None, 3600)

        self.mock_srv_resolver.resolve_service.return_value = [
            Server(
                host=b"srv.example.com", port=8448, priority=1, weight=1, expires=9999
            )
        ]

        uri = URI.fromBytes(b"matrix-federation://example.com/path", defaultPort=-1)
        result = await self.agent._route_matrix_uri(uri)
        self.assertEqual(result.target_host, b"srv.example.com")
        self.assertEqual(result.target_port, 8448)

    async def test_matrix_fed_srv_preferred_over_matrix_srv(self) -> None:
        """_matrix-fed._tcp SRV is tried first (Matrix 1.8)."""
        call_count = 0

        async def mock_resolve(service_name: bytes) -> list[Server]:
            nonlocal call_count
            call_count += 1
            if service_name == b"_matrix-fed._tcp.example.com":
                return [
                    Server(
                        host=b"fed.example.com",
                        port=443,
                        priority=1,
                        weight=1,
                        expires=9999,
                    )
                ]
            return []

        self.mock_srv_resolver.resolve_service = mock_resolve  # type: ignore[assignment]

        # Ensure no .well-known
        self.well_known_cache.set(b"example.com", None, 3600)

        uri = URI.fromBytes(b"matrix-federation://example.com/path", defaultPort=-1)
        result = await self.agent._route_matrix_uri(uri)
        self.assertEqual(result.target_host, b"fed.example.com")
        self.assertEqual(result.target_port, 443)
        # Should have only queried _matrix-fed (not _matrix) since it returned results.
        self.assertEqual(call_count, 1)

    async def test_fallback_to_legacy_matrix_srv(self) -> None:
        """When _matrix-fed._tcp returns nothing, _matrix._tcp is tried."""
        call_count = 0

        async def mock_resolve(service_name: bytes) -> list[Server]:
            nonlocal call_count
            call_count += 1
            if service_name == b"_matrix._tcp.example.com":
                return [
                    Server(
                        host=b"legacy.example.com",
                        port=8448,
                        priority=1,
                        weight=1,
                        expires=9999,
                    )
                ]
            return []

        self.mock_srv_resolver.resolve_service = mock_resolve  # type: ignore[assignment]
        self.well_known_cache.set(b"example.com", None, 3600)

        uri = URI.fromBytes(b"matrix-federation://example.com/path", defaultPort=-1)
        result = await self.agent._route_matrix_uri(uri)
        self.assertEqual(result.target_host, b"legacy.example.com")
        self.assertEqual(result.target_port, 8448)
        # Both SRV lookups should have been attempted.
        self.assertEqual(call_count, 2)

    async def test_no_srv_defaults_to_8448(self) -> None:
        """When no SRV records exist, default to port 8448 on the original host."""
        self.mock_srv_resolver.resolve_service.return_value = []
        self.well_known_cache.set(b"example.com", None, 3600)

        uri = URI.fromBytes(b"matrix-federation://example.com/path", defaultPort=-1)
        result = await self.agent._route_matrix_uri(uri)
        self.assertEqual(result.target_host, b"example.com")
        self.assertEqual(result.target_port, 8448)


class DoGetWellKnownTest(unittest.TestCase):
    """Tests for MatrixFederationAgent._do_get_well_known()."""

    def setUp(self) -> None:
        self.reactor = ResolvingMemoryReactorClock()
        self.reactor.seconds = lambda: 1000.0  # type: ignore[assignment]
        self.mock_srv_resolver = MagicMock(spec=SrvResolver)
        self.well_known_cache: TTLCache[bytes, bytes | None] = TTLCache("test-wk")

        self.agent = MatrixFederationAgent(
            reactor=self.reactor,
            tls_client_options_factory=None,
            _srv_resolver=self.mock_srv_resolver,
            _well_known_cache=self.well_known_cache,
        )

    @patch("sydent.http.matrixfederationagent.read_body_with_max_size")
    async def test_valid_well_known(self, mock_read_body: MagicMock) -> None:
        """A valid .well-known response returns the delegated server."""
        body = json.dumps({"m.server": "delegated.example.com:8448"}).encode()
        mock_read_body.return_value = defer.succeed(body)

        response = _make_well_known_response(status=200)
        self.agent._well_known_agent = MagicMock()
        self.agent._well_known_agent.request = AsyncMock(return_value=response)

        result, cache_period = await self.agent._do_get_well_known(b"example.com")
        self.assertEqual(result, b"delegated.example.com:8448")
        self.assertGreater(cache_period, 0)

    @patch("sydent.http.matrixfederationagent.read_body_with_max_size")
    async def test_non_200_returns_none(self, mock_read_body: MagicMock) -> None:
        """A non-200 response results in None (no delegation)."""
        mock_read_body.return_value = defer.succeed(b"Not Found")
        response = _make_well_known_response(status=404)
        self.agent._well_known_agent = MagicMock()
        self.agent._well_known_agent.request = AsyncMock(return_value=response)

        result, cache_period = await self.agent._do_get_well_known(b"example.com")
        self.assertIsNone(result)
        # Should cache the failure.
        self.assertGreaterEqual(cache_period, WELL_KNOWN_INVALID_CACHE_PERIOD)

    @patch("sydent.http.matrixfederationagent.read_body_with_max_size")
    async def test_invalid_json_returns_none(self, mock_read_body: MagicMock) -> None:
        """Invalid JSON in .well-known returns None."""
        mock_read_body.return_value = defer.succeed(b"not json at all")
        response = _make_well_known_response(status=200)
        self.agent._well_known_agent = MagicMock()
        self.agent._well_known_agent.request = AsyncMock(return_value=response)

        result, _ = await self.agent._do_get_well_known(b"example.com")
        self.assertIsNone(result)

    @patch("sydent.http.matrixfederationagent.read_body_with_max_size")
    async def test_missing_m_server_key(self, mock_read_body: MagicMock) -> None:
        """JSON without m.server key returns None."""
        mock_read_body.return_value = defer.succeed(
            json.dumps({"other": "key"}).encode()
        )
        response = _make_well_known_response(status=200)
        self.agent._well_known_agent = MagicMock()
        self.agent._well_known_agent.request = AsyncMock(return_value=response)

        result, _ = await self.agent._do_get_well_known(b"example.com")
        self.assertIsNone(result)

    @patch("sydent.http.matrixfederationagent.read_body_with_max_size")
    async def test_m_server_not_string(self, mock_read_body: MagicMock) -> None:
        """m.server with non-string value returns None."""
        mock_read_body.return_value = defer.succeed(
            json.dumps({"m.server": 12345}).encode()
        )
        response = _make_well_known_response(status=200)
        self.agent._well_known_agent = MagicMock()
        self.agent._well_known_agent.request = AsyncMock(return_value=response)

        result, _ = await self.agent._do_get_well_known(b"example.com")
        self.assertIsNone(result)

    @patch("sydent.http.matrixfederationagent.read_body_with_max_size")
    async def test_cache_control_max_age(self, mock_read_body: MagicMock) -> None:
        """Cache-Control max-age is respected, capped at WELL_KNOWN_MAX_CACHE_PERIOD."""
        body = json.dumps({"m.server": "delegated.example.com:8448"}).encode()
        mock_read_body.return_value = defer.succeed(body)
        response = _make_well_known_response(
            status=200,
            headers={b"cache-control": [b"max-age=7200"]},
        )
        self.agent._well_known_agent = MagicMock()
        self.agent._well_known_agent.request = AsyncMock(return_value=response)

        _, cache_period = await self.agent._do_get_well_known(b"example.com")
        self.assertEqual(cache_period, 7200)

    @patch("sydent.http.matrixfederationagent.read_body_with_max_size")
    async def test_cache_period_capped(self, mock_read_body: MagicMock) -> None:
        """Cache period is capped at WELL_KNOWN_MAX_CACHE_PERIOD (48h)."""
        body = json.dumps({"m.server": "delegated.example.com:8448"}).encode()
        mock_read_body.return_value = defer.succeed(body)
        # Request a cache period of 1 week.
        response = _make_well_known_response(
            status=200,
            headers={b"cache-control": [b"max-age=604800"]},
        )
        self.agent._well_known_agent = MagicMock()
        self.agent._well_known_agent.request = AsyncMock(return_value=response)

        _, cache_period = await self.agent._do_get_well_known(b"example.com")
        self.assertEqual(cache_period, WELL_KNOWN_MAX_CACHE_PERIOD)

    @patch("sydent.http.matrixfederationagent.read_body_with_max_size")
    async def test_request_exception_returns_none(
        self, mock_read_body: MagicMock
    ) -> None:
        """If the HTTP request itself throws, return None."""
        self.agent._well_known_agent = MagicMock()
        self.agent._well_known_agent.request = AsyncMock(
            side_effect=Exception("connection refused")
        )

        result, cache_period = await self.agent._do_get_well_known(b"example.com")
        self.assertIsNone(result)
        self.assertGreaterEqual(cache_period, WELL_KNOWN_INVALID_CACHE_PERIOD)


class GetWellKnownCachingTest(unittest.TestCase):
    """Tests for MatrixFederationAgent._get_well_known() caching behaviour."""

    def setUp(self) -> None:
        self.reactor = ResolvingMemoryReactorClock()
        self.reactor.seconds = lambda: 1000.0  # type: ignore[assignment]
        self.mock_srv_resolver = MagicMock(spec=SrvResolver)
        self.well_known_cache: TTLCache[bytes, bytes | None] = TTLCache("test-wk")

        self.agent = MatrixFederationAgent(
            reactor=self.reactor,
            tls_client_options_factory=None,
            _srv_resolver=self.mock_srv_resolver,
            _well_known_cache=self.well_known_cache,
        )

    async def test_cached_result_returned(self) -> None:
        """A cached .well-known result is returned without re-fetching."""
        self.well_known_cache.set(b"example.com", b"cached.example.com:8448", 3600)

        result = await self.agent._get_well_known(b"example.com")
        self.assertEqual(result, b"cached.example.com:8448")

    async def test_cached_none_returned(self) -> None:
        """A cached None (failed lookup) is returned."""
        self.well_known_cache.set(b"example.com", None, 3600)

        result = await self.agent._get_well_known(b"example.com")
        self.assertIsNone(result)

    @patch("sydent.http.matrixfederationagent.read_body_with_max_size")
    async def test_cache_miss_fetches(self, mock_read_body: MagicMock) -> None:
        """A cache miss triggers a fetch and caches the result."""
        body = json.dumps({"m.server": "new.example.com:8448"}).encode()
        mock_read_body.return_value = defer.succeed(body)
        response = _make_well_known_response(
            status=200,
            headers={b"cache-control": [b"max-age=3600"]},
        )
        self.agent._well_known_agent = MagicMock()
        self.agent._well_known_agent.request = AsyncMock(return_value=response)

        result = await self.agent._get_well_known(b"fresh.example.com")
        self.assertEqual(result, b"new.example.com:8448")

        # The result should now be in cache.
        cached = self.well_known_cache.get(b"fresh.example.com")
        self.assertEqual(cached, b"new.example.com:8448")
