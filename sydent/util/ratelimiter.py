# Copyright 2025 New Vector Ltd.
# Copyright 2022 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import asyncio
import logging
from http import HTTPStatus
from typing import Generic, TypeVar

from sydent.http.servlets import MatrixRestError

logger = logging.getLogger(__name__)

K = TypeVar("K")


class LimitExceededException(MatrixRestError):
    def __init__(self, error: str | None = None) -> None:
        if error is None:
            error = "Too many requests"

        super().__init__(HTTPStatus.TOO_MANY_REQUESTS, "M_UNKNOWN", error)


class Ratelimiter(Generic[K]):
    """A ratelimiter based on leaky token bucket algorithm.

    Args:
        burst: the number of requests that can happen at once before we start
            ratelimiting
        rate_hz: The maximum average sustained rate in hertz of requests we'll
            accept.
    """

    def __init__(self, burst: int, rate_hz: float) -> None:
        # The "burst" count (or the capacity of each bucket in leaky bucket
        # algorithm).
        self._burst = burst

        # A map from key to number of tokens in its bucket. We ratelimit when
        # the number of tokens is greater than `burst`.
        #
        # Entries are removed when token count hits zero.
        self._buckets: dict[K, int] = {}

        self._rate_hz = rate_hz
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background task that drains tokens."""
        self._task = asyncio.ensure_future(self._drain_loop())

    async def stop(self) -> None:
        """Stop the background drain task."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _drain_loop(self) -> None:
        """Periodically remove tokens from all active buckets."""
        interval = 1.0 / self._rate_hz
        while True:
            await asyncio.sleep(interval)
            self._periodic_call()

    def _periodic_call(self) -> None:
        # Take one away from all active buckets. If a bucket reaches zero then
        # remove it from the dict.
        self._buckets = {
            key: tokens - 1 for key, tokens in self._buckets.items() if tokens > 1
        }

    def ratelimit(self, key: K, error: str | None = None) -> None:
        """Check if we should ratelimit the request with the given key.

        Raises:
            LimitExceededException: if the request should be denied.
        """
        if error is None:
            error = "Too many requests"

        # We get the current token count and compare it with the `burst`.
        current_tokens = self._buckets.get(key, 0)
        if current_tokens >= self._burst:
            logger.warning("Ratelimit hit: %s: %s", error, key)
            raise LimitExceededException(error)

        self._buckets[key] = current_tokens + 1
