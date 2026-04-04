# Copyright 2019-2025 New Vector Ltd.
# Copyright 2014-2016 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
import random
import time
from collections.abc import Callable
from typing import SupportsInt

import attr
import dns.asyncresolver
import dns.exception
import dns.name
import dns.rdatatype

logger = logging.getLogger(__name__)

SERVER_CACHE: dict[bytes, list["Server"]] = {}


@attr.s(frozen=True, slots=True, auto_attribs=True)
class Server:
    """
    Our record of an individual server which can be tried to reach a destination.
    Attributes:
        host (bytes): target hostname
        port (int):
        priority (int):
        weight (int):
        expires (int): when the cache should expire this record - in *seconds* since
            the epoch
    """

    host: bytes
    port: int
    priority: int = 0
    weight: int = 0
    expires: int = 0


def pick_server_from_list(server_list: list[Server]) -> tuple[bytes, int]:
    """Randomly choose a server from the server list.

    :param server_list: List of candidate servers.

    :returns a (host, port) pair for the chosen server.
    """
    if not server_list:
        raise RuntimeError("pick_server_from_list called with empty list")

    # TODO: currently we only use the lowest-priority servers. We should maintain a
    # cache of servers known to be "down" and filter them out

    min_priority = min(s.priority for s in server_list)
    eligible_servers = [s for s in server_list if s.priority == min_priority]
    total_weight = sum(s.weight for s in eligible_servers)
    target_weight = random.randint(0, total_weight)

    for s in eligible_servers:
        target_weight -= s.weight

        if target_weight <= 0:
            return s.host, s.port

    # this should be impossible.
    raise RuntimeError(
        "pick_server_from_list got to end of eligible server list.",
    )


class SrvResolver:
    """Interface to the dns client to do SRV lookups, with result caching.

    The default resolver in dnspython doesn't do any caching, so we add our own
    caching layer here.

    :param cache: cache object

    :param get_time: Clock implementation. Should return seconds since the epoch.
    """

    def __init__(
        self,
        cache: dict[bytes, list[Server]] = SERVER_CACHE,
        get_time: Callable[[], SupportsInt] = time.time,
    ) -> None:
        self._cache = cache
        self._get_time = get_time
        self._resolver = dns.asyncresolver.Resolver()

    async def resolve_service(self, service_name: bytes) -> list["Server"]:
        """Look up a SRV record

        :param service_name: The record to look up.

        :returns a list of the SRV records, or an empty list if none found.
        """
        now = int(self._get_time())

        cache_entry = self._cache.get(service_name, None)
        if cache_entry:
            if all(s.expires > now for s in cache_entry):
                servers = list(cache_entry)
                return servers

        try:
            answers = await self._resolver.resolve(service_name.decode(), "SRV")
        except dns.resolver.NXDOMAIN:
            # TODO: cache this. We can get the SOA out of the exception, and use
            # the negative-TTL value.
            return []
        except dns.exception.DNSException as e:
            # We failed to resolve the name (other than NXDOMAIN).
            # Try something in the cache, else reraise.
            cache_entry = self._cache.get(service_name, None)
            if cache_entry:
                logger.warning(
                    "Failed to resolve %r, falling back to cache. %r",
                    service_name,
                    e,
                )
                return list(cache_entry)
            else:
                raise e

        # Check for "." target meaning the service is explicitly unavailable
        rdata_list = list(answers)
        if len(rdata_list) == 1 and str(rdata_list[0].target) == ".":
            raise OSError(f"Service {service_name.decode()} unavailable")

        servers = []
        ttl = answers.rrset.ttl if answers.rrset is not None else 0

        for rdata in answers:
            servers.append(
                Server(
                    host=str(rdata.target).rstrip(".").encode(),
                    port=rdata.port,
                    priority=rdata.priority,
                    weight=rdata.weight,
                    expires=now + ttl,
                )
            )

        self._cache[service_name] = list(servers)
        return servers
