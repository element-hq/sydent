# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.

from unittest.mock import AsyncMock, MagicMock, patch

import dns.name
import dns.resolver
import pytest

from sydent.http.srvresolver import Server, SrvResolver, pick_server_from_list


def _make_srv_rdata(host: str, port: int, priority: int = 0, weight: int = 0):
    """Build a mock SRV rdata object."""
    rdata = MagicMock()
    rdata.target = dns.name.from_text(host)
    rdata.port = port
    rdata.priority = priority
    rdata.weight = weight
    return rdata


def _make_answer(rdata_list, ttl: int = 300):
    """Build a mock dns.resolver.Answer."""
    answer = MagicMock()
    answer.__iter__ = MagicMock(side_effect=lambda: iter(rdata_list))
    rrset = MagicMock()
    rrset.ttl = ttl
    answer.rrset = rrset
    return answer


# --- pick_server_from_list tests ---


def test_pick_single_server():
    s = Server(host=b"only.example.com", port=8448, priority=10, weight=1)
    host, port = pick_server_from_list([s])
    assert host == b"only.example.com"
    assert port == 8448


def test_pick_empty_list_raises():
    with pytest.raises(RuntimeError):
        pick_server_from_list([])


def test_pick_priority_ordering():
    low = Server(host=b"low.example.com", port=1, priority=10, weight=1)
    high = Server(host=b"high.example.com", port=2, priority=20, weight=1)
    for _ in range(100):
        host, _ = pick_server_from_list([low, high])
        assert host == b"low.example.com"


def test_pick_weight_distribution():
    """Within same priority, servers are chosen proportionally to weight."""
    heavy = Server(host=b"heavy.example.com", port=1, priority=10, weight=90)
    light = Server(host=b"light.example.com", port=2, priority=10, weight=10)
    counts: dict[bytes, int] = {b"heavy.example.com": 0, b"light.example.com": 0}
    for _ in range(1000):
        host, _ = pick_server_from_list([heavy, light])
        counts[host] += 1
    assert counts[b"heavy.example.com"] > 600
    assert counts[b"light.example.com"] < 400


def test_pick_zero_weight():
    """A zero-weight server can still be chosen without crashing."""
    zero = Server(host=b"zero.example.com", port=1, priority=10, weight=0)
    nonzero = Server(host=b"nonzero.example.com", port=2, priority=10, weight=100)
    for _ in range(50):
        pick_server_from_list([zero, nonzero])


# --- SrvResolver tests ---


async def test_resolve_service_basic():
    cache: dict[bytes, list[Server]] = {}
    resolver = SrvResolver(cache=cache, get_time=lambda: 1000)

    rdata = [_make_srv_rdata("srv1.example.com.", 8448, priority=10, weight=5)]
    answer = _make_answer(rdata, ttl=600)

    with patch.object(
        resolver._resolver, "resolve", new_callable=AsyncMock, return_value=answer
    ):
        servers = await resolver.resolve_service(b"_matrix._tcp.example.com")

    assert len(servers) == 1
    assert servers[0].host == b"srv1.example.com"
    assert servers[0].port == 8448
    assert servers[0].priority == 10
    assert servers[0].expires == 1600


async def test_resolve_service_caching():
    cache: dict[bytes, list[Server]] = {}
    current_time = 1000
    resolver = SrvResolver(cache=cache, get_time=lambda: current_time)

    rdata = [_make_srv_rdata("cached.example.com.", 8448)]
    answer = _make_answer(rdata, ttl=600)
    mock_resolve = AsyncMock(return_value=answer)

    with patch.object(resolver._resolver, "resolve", mock_resolve):
        first = await resolver.resolve_service(b"_matrix._tcp.example.com")
        second = await resolver.resolve_service(b"_matrix._tcp.example.com")

    assert first == second
    assert mock_resolve.await_count == 1


async def test_resolve_service_name_error():
    cache: dict[bytes, list[Server]] = {}
    resolver = SrvResolver(cache=cache, get_time=lambda: 1000)

    with patch.object(
        resolver._resolver,
        "resolve",
        new_callable=AsyncMock,
        side_effect=dns.resolver.NXDOMAIN(),
    ):
        servers = await resolver.resolve_service(b"_matrix._tcp.nonexistent.com")

    assert servers == []


async def test_resolve_service_dns_error_with_cache_fallback():
    cache: dict[bytes, list[Server]] = {
        b"_matrix._tcp.example.com": [
            Server(host=b"cached.example.com", port=8448, expires=500)
        ]
    }
    resolver = SrvResolver(cache=cache, get_time=lambda: 1000)

    with patch.object(
        resolver._resolver,
        "resolve",
        new_callable=AsyncMock,
        side_effect=dns.resolver.NoNameservers(),
    ):
        servers = await resolver.resolve_service(b"_matrix._tcp.example.com")

    assert len(servers) == 1
    assert servers[0].host == b"cached.example.com"


async def test_resolve_service_dns_error_no_cache_raises():
    cache: dict[bytes, list[Server]] = {}
    resolver = SrvResolver(cache=cache, get_time=lambda: 1000)

    with patch.object(
        resolver._resolver,
        "resolve",
        new_callable=AsyncMock,
        side_effect=dns.resolver.NoNameservers(),
    ):
        with pytest.raises(dns.resolver.NoNameservers):
            await resolver.resolve_service(b"_matrix._tcp.nocache.com")
