# Copyright 2025 New Vector Ltd.
# Copyright 2022 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import pytest

from sydent.util.ratelimiter import LimitExceededException, Ratelimiter


def _make_ratelimiter(burst: int = 5, rate_hz: float = 0.5) -> Ratelimiter[str]:
    """Create a Ratelimiter for testing without starting the async drain loop."""
    rl: Ratelimiter[str] = object.__new__(Ratelimiter)
    rl._burst = burst
    rl._buckets = {}
    rl._rate_hz = rate_hz
    rl._task = None
    return rl


def test_simple() -> None:
    """A single request doesn't get ratelimited."""
    rl = _make_ratelimiter(burst=5)
    rl.ratelimit("key")


def test_burst() -> None:
    """We can send `burst` messages before getting ratelimited."""
    rl = _make_ratelimiter(burst=5)

    for _ in range(5):
        rl.ratelimit("key")

    with pytest.raises(LimitExceededException):
        rl.ratelimit("key")


def test_burst_reset() -> None:
    """After hitting the limit, draining buckets allows sending again."""
    rl = _make_ratelimiter(burst=5)

    for _ in range(5):
        rl.ratelimit("key")

    with pytest.raises(LimitExceededException):
        rl.ratelimit("key")

    # Drain all tokens
    for _ in range(10):
        rl._periodic_call()

    # Should be able to send again
    for _ in range(5):
        rl.ratelimit("key")

    with pytest.raises(LimitExceededException):
        rl.ratelimit("key")


def test_average_rate() -> None:
    """Sending faster than the drain rate eventually gets ratelimited."""
    rl = _make_ratelimiter(burst=5)

    # Send 2 requests per drain cycle — net +1 per cycle, fills burst after 5 cycles
    with pytest.raises(LimitExceededException):
        for _ in range(100):
            rl._periodic_call()
            rl.ratelimit("key")
            rl.ratelimit("key")


def test_sustained_rate_within_limit() -> None:
    """Sending at exactly the drain rate never gets limited (after burst fills)."""
    rl = _make_ratelimiter(burst=5)

    # Fill burst
    for _ in range(5):
        rl.ratelimit("key")

    # Now send 1 per drain — bucket stays at 5 (drain 1, add 1)
    for _ in range(100):
        rl._periodic_call()
        rl.ratelimit("key")
