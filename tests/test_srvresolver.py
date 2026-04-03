# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.

from unittest.mock import AsyncMock

from twisted.internet.error import ConnectError
from twisted.names import dns
from twisted.names.error import DNSNameError, DomainError
from twisted.trial import unittest

from sydent.http.srvresolver import Server, SrvResolver, pick_server_from_list


def _make_srv_answer(
    host: bytes, port: int, priority: int = 0, weight: int = 0, ttl: int = 300
) -> dns.RRHeader:
    """Build an RRHeader wrapping a Record_SRV, as twisted.names would return."""
    payload = dns.Record_SRV(
        priority=priority,
        weight=weight,
        port=port,
        target=host,
    )
    return dns.RRHeader(
        type=dns.SRV,
        payload=payload,
        ttl=ttl,
    )


class PickServerFromListTest(unittest.TestCase):
    """Tests for pick_server_from_list()."""

    def test_single_server(self) -> None:
        """When the list has one entry, that entry is always returned."""
        s = Server(host=b"only.example.com", port=8448, priority=10, weight=1)
        host, port = pick_server_from_list([s])
        self.assertEqual(host, b"only.example.com")
        self.assertEqual(port, 8448)

    def test_empty_list_raises(self) -> None:
        """An empty list should raise RuntimeError."""
        self.assertRaises(RuntimeError, pick_server_from_list, [])

    def test_priority_ordering(self) -> None:
        """Only the lowest-priority servers should be selected."""
        low = Server(host=b"low.example.com", port=1, priority=10, weight=1)
        high = Server(host=b"high.example.com", port=2, priority=20, weight=1)
        # Run many times — the high-priority server should never appear.
        for _ in range(100):
            host, port = pick_server_from_list([low, high])
            self.assertEqual(host, b"low.example.com")

    def test_weight_distribution(self) -> None:
        """Within the same priority, servers are chosen proportionally to weight."""
        heavy = Server(host=b"heavy.example.com", port=1, priority=10, weight=90)
        light = Server(host=b"light.example.com", port=2, priority=10, weight=10)
        counts: dict[bytes, int] = {b"heavy.example.com": 0, b"light.example.com": 0}
        trials = 1000
        for _ in range(trials):
            host, _ = pick_server_from_list([heavy, light])
            counts[host] += 1
        # Heavy should get roughly 90% — allow wide margin for randomness.
        self.assertGreater(counts[b"heavy.example.com"], trials * 0.7)
        self.assertLess(counts[b"light.example.com"], trials * 0.3)

    def test_zero_weight(self) -> None:
        """A zero-weight server can still be chosen (RFC 2782 says small chance)."""
        zero = Server(host=b"zero.example.com", port=1, priority=10, weight=0)
        nonzero = Server(host=b"nonzero.example.com", port=2, priority=10, weight=100)
        # With weight=0 vs 100, the zero server should very rarely be picked.
        # Just verify it doesn't crash.
        for _ in range(50):
            pick_server_from_list([zero, nonzero])


class SrvResolverTest(unittest.TestCase):
    """Tests for SrvResolver.resolve_service()."""

    def setUp(self) -> None:
        self.mock_lookup = AsyncMock()
        self.cache: dict[bytes, list[Server]] = {}
        self.current_time = 1000
        self.resolver = SrvResolver(
            lookup_service=self.mock_lookup,
            cache=self.cache,
            get_time=lambda: self.current_time,
        )

    async def test_basic_lookup(self) -> None:
        """A successful SRV lookup returns Server objects with correct fields."""
        self.mock_lookup.return_value = (
            [
                _make_srv_answer(
                    b"srv1.example.com", 8448, priority=10, weight=5, ttl=600
                ),
                _make_srv_answer(
                    b"srv2.example.com", 8449, priority=20, weight=10, ttl=300
                ),
            ],
            [],
            [],
        )

        servers = await self.resolver.resolve_service(b"_matrix._tcp.example.com")

        self.assertEqual(len(servers), 2)
        self.assertEqual(servers[0].host, b"srv1.example.com")
        self.assertEqual(servers[0].port, 8448)
        self.assertEqual(servers[0].priority, 10)
        self.assertEqual(servers[0].weight, 5)
        self.assertEqual(servers[0].expires, 1600)  # 1000 + 600
        self.assertEqual(servers[1].host, b"srv2.example.com")
        self.assertEqual(servers[1].port, 8449)

        self.mock_lookup.assert_awaited_once_with("_matrix._tcp.example.com")

    async def test_results_are_cached(self) -> None:
        """Subsequent calls within TTL return cached results without re-querying DNS."""
        self.mock_lookup.return_value = (
            [_make_srv_answer(b"cached.example.com", 8448, ttl=600)],
            [],
            [],
        )

        first = await self.resolver.resolve_service(b"_matrix._tcp.example.com")
        self.assertEqual(len(first), 1)

        # Advance time but stay within TTL.
        self.current_time = 1500

        second = await self.resolver.resolve_service(b"_matrix._tcp.example.com")
        self.assertEqual(second, first)

        # DNS should only have been queried once.
        self.assertEqual(self.mock_lookup.await_count, 1)

    async def test_cache_expires(self) -> None:
        """After TTL expires, DNS is queried again."""
        self.mock_lookup.return_value = (
            [_make_srv_answer(b"old.example.com", 8448, ttl=600)],
            [],
            [],
        )

        await self.resolver.resolve_service(b"_matrix._tcp.example.com")

        # Advance past TTL.
        self.current_time = 1700

        self.mock_lookup.return_value = (
            [_make_srv_answer(b"new.example.com", 8448, ttl=600)],
            [],
            [],
        )

        servers = await self.resolver.resolve_service(b"_matrix._tcp.example.com")
        self.assertEqual(servers[0].host, b"new.example.com")
        self.assertEqual(self.mock_lookup.await_count, 2)

    async def test_dns_name_error_returns_empty(self) -> None:
        """DNSNameError (name doesn't exist) returns an empty list."""
        self.mock_lookup.side_effect = DNSNameError()

        servers = await self.resolver.resolve_service(b"_matrix._tcp.nonexistent.com")
        self.assertEqual(servers, [])

    async def test_domain_error_falls_back_to_cache(self) -> None:
        """On transient DNS failure, stale cached entries are returned."""
        # First call succeeds and populates cache.
        self.mock_lookup.return_value = (
            [_make_srv_answer(b"cached.example.com", 8448, ttl=60)],
            [],
            [],
        )
        await self.resolver.resolve_service(b"_matrix._tcp.example.com")

        # Advance past TTL.
        self.current_time = 1200

        # Second call fails with transient error.
        self.mock_lookup.side_effect = DomainError()

        servers = await self.resolver.resolve_service(b"_matrix._tcp.example.com")
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].host, b"cached.example.com")

    async def test_domain_error_no_cache_raises(self) -> None:
        """DomainError with no cache entry re-raises."""
        self.mock_lookup.side_effect = DomainError()

        with self.assertRaises(DomainError):
            await self.resolver.resolve_service(b"_matrix._tcp.nocache.com")

    async def test_service_unavailable_target_dot(self) -> None:
        """A single SRV record pointing to '.' means the service is unavailable."""
        self.mock_lookup.return_value = (
            [_make_srv_answer(b".", 0, ttl=600)],
            [],
            [],
        )

        with self.assertRaises(ConnectError):
            await self.resolver.resolve_service(b"_matrix._tcp.example.com")

    async def test_non_srv_records_filtered(self) -> None:
        """Non-SRV answer records are ignored."""
        srv = _make_srv_answer(b"real.example.com", 8448, ttl=300)
        # Create a non-SRV record (e.g., A record type).
        non_srv = dns.RRHeader(type=dns.A, payload=None, ttl=300)

        self.mock_lookup.return_value = ([srv, non_srv], [], [])

        servers = await self.resolver.resolve_service(b"_matrix._tcp.example.com")
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].host, b"real.example.com")
