# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.

import json
from unittest.mock import AsyncMock, MagicMock

from multidict import CIMultiDict

from sydent.http.matrixfederationagent import (
    WELL_KNOWN_INVALID_CACHE_PERIOD,
    WELL_KNOWN_MAX_CACHE_PERIOD,
    MatrixFederationAgent,
    _cache_period_from_headers,
    _parse_cache_control,
)
from sydent.http.srvresolver import Server, SrvResolver
from sydent.util.ttlcache import TTLCache

# --- _parse_cache_control tests ---


class TestParseCacheControl:
    def test_max_age(self):
        headers = CIMultiDict({"Cache-Control": "max-age=3600"})
        result = _parse_cache_control(headers)
        assert result[b"max-age"] == b"3600"

    def test_multiple_directives(self):
        headers = CIMultiDict({"Cache-Control": "no-store, max-age=300"})
        result = _parse_cache_control(headers)
        assert b"no-store" in result
        assert result[b"max-age"] == b"300"

    def test_no_value_directive(self):
        headers = CIMultiDict({"Cache-Control": "no-cache"})
        result = _parse_cache_control(headers)
        assert b"no-cache" in result
        assert result[b"no-cache"] is None

    def test_empty_headers(self):
        headers = CIMultiDict()
        result = _parse_cache_control(headers)
        assert result == {}


# --- _cache_period_from_headers tests ---


class TestCachePeriodFromHeaders:
    def test_max_age(self):
        headers = CIMultiDict({"Cache-Control": "max-age=7200"})
        assert _cache_period_from_headers(headers) == 7200

    def test_no_store(self):
        headers = CIMultiDict({"Cache-Control": "no-store"})
        assert _cache_period_from_headers(headers) == 0

    def test_expires_header(self):
        headers = CIMultiDict({"Expires": "Thu, 01 Jan 2099 00:00:00 GMT"})
        period = _cache_period_from_headers(headers, time_now=lambda: 0)
        assert period is not None and period > 0

    def test_invalid_expires(self):
        headers = CIMultiDict({"Expires": "0"})
        assert _cache_period_from_headers(headers, time_now=lambda: 1000) == 0

    def test_no_cache_headers(self):
        headers = CIMultiDict()
        assert _cache_period_from_headers(headers) is None

    def test_max_age_takes_precedence_over_expires(self):
        headers = CIMultiDict(
            {"Cache-Control": "max-age=100", "Expires": "Thu, 01 Jan 2099 00:00:00 GMT"}
        )
        assert _cache_period_from_headers(headers) == 100


# --- Helpers for MatrixFederationAgent tests ---


def _make_agent(
    srv_resolver=None,
    well_known_cache: TTLCache | None = None,
) -> MatrixFederationAgent:
    """Create a MatrixFederationAgent with mocked internals."""
    agent = MatrixFederationAgent.__new__(MatrixFederationAgent)
    if srv_resolver is not None:
        agent._srv_resolver = srv_resolver
    else:
        mock_srv = MagicMock()
        mock_srv.resolve_service = AsyncMock(return_value=[])
        agent._srv_resolver = mock_srv
    agent._well_known_cache = well_known_cache or TTLCache("test-wk")
    agent._session = MagicMock()
    agent._tls_client_options_factory = None
    return agent


def _make_session_mock(body: bytes, status: int = 200, headers=None):
    """Create a mock session.get() that returns an async context manager response."""
    mock_response = MagicMock()
    mock_response.status = status
    mock_response.read = AsyncMock(return_value=body)
    mock_response.headers = headers or CIMultiDict()
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)
    return mock_session


# --- _route_matrix_uri tests ---


async def test_route_ip_literal_default_port():
    agent = _make_agent()
    result = await agent._route_matrix_uri(b"1.2.3.4", -1, b"/path")
    assert result.target_host == b"1.2.3.4"
    assert result.target_port == 8448
    assert result.tls_server_name == b"1.2.3.4"


async def test_route_ip_literal_explicit_port():
    agent = _make_agent()
    result = await agent._route_matrix_uri(b"1.2.3.4", 9999, b"/path")
    assert result.target_host == b"1.2.3.4"
    assert result.target_port == 9999


async def test_route_explicit_port_skips_srv():
    agent = _make_agent()
    result = await agent._route_matrix_uri(b"example.com", 8449, b"/path")
    assert result.target_host == b"example.com"
    assert result.target_port == 8449
    assert result.host_header == b"example.com:8449"
    agent._srv_resolver.resolve_service.assert_not_awaited()


async def test_route_well_known_delegates():
    cache: TTLCache[bytes, bytes | None] = TTLCache("test")
    cache.set(b"example.com", b"delegated.example.com:8448", 3600)
    agent = _make_agent(well_known_cache=cache)

    result = await agent._route_matrix_uri(b"example.com", -1, b"/path")
    assert result.target_host == b"delegated.example.com"
    assert result.target_port == 8448


async def test_route_well_known_no_port_falls_to_srv():
    cache: TTLCache[bytes, bytes | None] = TTLCache("test")
    cache.set(b"example.com", b"delegated.example.com", 3600)
    srv = MagicMock(spec=SrvResolver)
    srv.resolve_service = AsyncMock(
        return_value=[
            Server(
                host=b"srv.example.com", port=443, priority=1, weight=1, expires=9999
            )
        ]
    )
    agent = _make_agent(srv_resolver=srv, well_known_cache=cache)

    result = await agent._route_matrix_uri(b"example.com", -1, b"/path")
    assert result.target_host == b"srv.example.com"
    assert result.target_port == 443


async def test_route_well_known_none_falls_to_srv():
    cache: TTLCache[bytes, bytes | None] = TTLCache("test")
    cache.set(b"example.com", None, 3600)
    srv = MagicMock(spec=SrvResolver)
    srv.resolve_service = AsyncMock(
        return_value=[
            Server(
                host=b"srv.example.com", port=8448, priority=1, weight=1, expires=9999
            )
        ]
    )
    agent = _make_agent(srv_resolver=srv, well_known_cache=cache)

    result = await agent._route_matrix_uri(b"example.com", -1, b"/path")
    assert result.target_host == b"srv.example.com"
    assert result.target_port == 8448


async def test_route_matrix_fed_srv_preferred():
    cache: TTLCache[bytes, bytes | None] = TTLCache("test")
    cache.set(b"example.com", None, 3600)

    call_count = 0

    async def mock_resolve(service_name: bytes):
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

    srv = MagicMock(spec=SrvResolver)
    srv.resolve_service = mock_resolve
    agent = _make_agent(srv_resolver=srv, well_known_cache=cache)

    result = await agent._route_matrix_uri(b"example.com", -1, b"/path")
    assert result.target_host == b"fed.example.com"
    assert result.target_port == 443
    assert call_count == 1  # Only _matrix-fed was queried


async def test_route_fallback_to_legacy_matrix_srv():
    cache: TTLCache[bytes, bytes | None] = TTLCache("test")
    cache.set(b"example.com", None, 3600)

    call_count = 0

    async def mock_resolve(service_name: bytes):
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

    srv = MagicMock(spec=SrvResolver)
    srv.resolve_service = mock_resolve
    agent = _make_agent(srv_resolver=srv, well_known_cache=cache)

    result = await agent._route_matrix_uri(b"example.com", -1, b"/path")
    assert result.target_host == b"legacy.example.com"
    assert result.target_port == 8448
    assert call_count == 2  # Both lookups attempted


async def test_route_no_srv_defaults_to_8448():
    cache: TTLCache[bytes, bytes | None] = TTLCache("test")
    cache.set(b"example.com", None, 3600)
    agent = _make_agent(well_known_cache=cache)

    result = await agent._route_matrix_uri(b"example.com", -1, b"/path")
    assert result.target_host == b"example.com"
    assert result.target_port == 8448


# --- _do_get_well_known tests ---


async def test_well_known_valid():
    body = json.dumps({"m.server": "delegated.example.com:8448"}).encode()
    agent = _make_agent()
    agent._session = _make_session_mock(body, status=200)

    result, cache_period = await agent._do_get_well_known(b"example.com")
    assert result == b"delegated.example.com:8448"
    assert cache_period > 0


async def test_well_known_not_found():
    agent = _make_agent()
    agent._session = _make_session_mock(b"Not Found", status=404)

    result, cache_period = await agent._do_get_well_known(b"example.com")
    assert result is None
    assert cache_period >= WELL_KNOWN_INVALID_CACHE_PERIOD


async def test_well_known_invalid_json():
    agent = _make_agent()
    agent._session = _make_session_mock(b"not json", status=200)

    result, _ = await agent._do_get_well_known(b"example.com")
    assert result is None


async def test_well_known_missing_m_server():
    body = json.dumps({"other": "key"}).encode()
    agent = _make_agent()
    agent._session = _make_session_mock(body, status=200)

    result, _ = await agent._do_get_well_known(b"example.com")
    assert result is None


async def test_well_known_m_server_not_string():
    body = json.dumps({"m.server": 12345}).encode()
    agent = _make_agent()
    agent._session = _make_session_mock(body, status=200)

    result, _ = await agent._do_get_well_known(b"example.com")
    assert result is None


async def test_well_known_cache_control_max_age():
    body = json.dumps({"m.server": "delegated.example.com:8448"}).encode()
    headers = CIMultiDict({"Cache-Control": "max-age=7200"})
    agent = _make_agent()
    agent._session = _make_session_mock(body, status=200, headers=headers)

    _, cache_period = await agent._do_get_well_known(b"example.com")
    assert cache_period == 7200


async def test_well_known_cache_period_capped():
    body = json.dumps({"m.server": "delegated.example.com:8448"}).encode()
    headers = CIMultiDict({"Cache-Control": "max-age=604800"})  # 1 week
    agent = _make_agent()
    agent._session = _make_session_mock(body, status=200, headers=headers)

    _, cache_period = await agent._do_get_well_known(b"example.com")
    assert cache_period == WELL_KNOWN_MAX_CACHE_PERIOD


async def test_well_known_request_exception():
    agent = _make_agent()
    mock_session = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_ctx)
    agent._session = mock_session

    result, cache_period = await agent._do_get_well_known(b"example.com")
    assert result is None
    assert cache_period >= WELL_KNOWN_INVALID_CACHE_PERIOD


# --- _get_well_known caching tests ---


async def test_get_well_known_cached_result():
    cache: TTLCache[bytes, bytes | None] = TTLCache("test")
    cache.set(b"example.com", b"cached.example.com:8448", 3600)
    agent = _make_agent(well_known_cache=cache)

    result = await agent._get_well_known(b"example.com")
    assert result == b"cached.example.com:8448"


async def test_get_well_known_cached_none():
    cache: TTLCache[bytes, bytes | None] = TTLCache("test")
    cache.set(b"example.com", None, 3600)
    agent = _make_agent(well_known_cache=cache)

    result = await agent._get_well_known(b"example.com")
    assert result is None


async def test_get_well_known_cache_miss_fetches():
    body = json.dumps({"m.server": "new.example.com:8448"}).encode()
    headers = CIMultiDict({"Cache-Control": "max-age=3600"})
    cache: TTLCache[bytes, bytes | None] = TTLCache("test")
    agent = _make_agent(well_known_cache=cache)
    agent._session = _make_session_mock(body, status=200, headers=headers)

    # _do_get_well_known should return the result and a cache period
    result, cache_period = await agent._do_get_well_known(b"fresh.example.com")
    assert result == b"new.example.com:8448"
    assert cache_period == 3600
