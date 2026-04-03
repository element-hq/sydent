# Copyright 2025 New Vector Ltd.
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
from typing import Any

import aiohttp.resolver
from netaddr import IPAddress, IPSet

logger = logging.getLogger(__name__)


def check_against_blacklist(
    ip_address: IPAddress, ip_whitelist: IPSet | None, ip_blacklist: IPSet
) -> bool:
    """
    Compares an IP address to allowed and disallowed IP sets.

    Args:
        ip_address: The IP address to check
        ip_whitelist: Allowed IP addresses.
        ip_blacklist: Disallowed IP addresses.

    Returns:
        True if the IP address is in the blacklist and not in the whitelist.
    """
    if ip_address in ip_blacklist:
        if ip_whitelist is None or ip_address not in ip_whitelist:
            return True
    return False


class BlacklistingResolver(aiohttp.resolver.AbstractResolver):
    """Custom aiohttp resolver that filters out blacklisted IPs."""

    def __init__(
        self,
        ip_whitelist: IPSet | None,
        ip_blacklist: IPSet,
    ) -> None:
        self._inner: aiohttp.resolver.DefaultResolver | None = None
        self._ip_whitelist = ip_whitelist
        self._ip_blacklist = ip_blacklist

    def _get_inner(self) -> aiohttp.resolver.DefaultResolver:
        if self._inner is None:
            self._inner = aiohttp.resolver.DefaultResolver()
        return self._inner

    async def resolve(
        self, host: str, port: int = 0, family: int = 0
    ) -> list[dict[str, Any]]:
        results = await self._get_inner().resolve(host, port, family)
        filtered = []
        for r in results:
            ip = IPAddress(r["host"])
            if not check_against_blacklist(ip, self._ip_whitelist, self._ip_blacklist):
                filtered.append(r)
            else:
                logger.info(
                    "Dropped %s from DNS resolution to %s due to blacklist",
                    ip,
                    host,
                )
        if not filtered:
            raise OSError(f"DNS lookup for {host} returned only blacklisted addresses")
        return filtered

    async def close(self) -> None:
        if self._inner is not None:
            await self._inner.close()
