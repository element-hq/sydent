# Copyright 2025 New Vector Ltd.
# Copyright 2022 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
from http import HTTPStatus
from typing import Dict, Generic, Optional, TypeVar

from twisted.internet import task
from twisted.internet.interfaces import IReactorTime

from sydent.http.servlets import MatrixRestError

logger = logging.getLogger(__name__)

K = TypeVar("K")


class LimitExceededException(MatrixRestError):
    def __init__(self, error: Optional[str] = None) -> None:
        if error is None:
            error = "Too many requests"

        super().__init__(HTTPStatus.TOO_MANY_REQUESTS, "M_UNKNOWN", error)


class Ratelimiter(Generic[K]):
    """A ratelimiter based on leaky token bucket algorithm.

    Args:
        reactor
        burst: the number of requests that can happen at once before we start
            ratelimiting
        rate_hz: The maximum average sustained rate in hertz of requests we'll
            accept.
    """

    def __init__(self, reactor: IReactorTime, burst: int, rate_hz: float) -> None:
        # The "burst" count (or the capacity of each bucket in leaky bucket
        # algorithm).
        self._burst = burst

        # A map from key to number of tokens in its bucket. We ratelimit when
        # the number of tokens is greater than `burst`.
        #
        # Entries are removed when token count hits zero.
        self._buckets: Dict[K, int] = {}

        # We remove tokens from all buckets at `rate_hz` hertz.
        call = task.LoopingCall(self._periodic_call)
        call.clock = reactor
        call.start(1 / rate_hz)

    def _periodic_call(self) -> None:
        # Take one away from all active buckets. If a bucket reaches zero then
        # remove it from the dict.
        self._buckets = {
            key: tokens - 1 for key, tokens in self._buckets.items() if tokens > 1
        }

    def ratelimit(self, key: K, error: Optional[str] = None) -> None:
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
