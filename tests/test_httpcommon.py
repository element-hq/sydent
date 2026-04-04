# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.

from unittest.mock import AsyncMock, MagicMock

import pytest

from sydent.http.httpcommon import BodyExceededMaxSize, read_body_with_max_size


def _mock_response(content: bytes) -> MagicMock:
    """Create a mock aiohttp.ClientResponse."""
    response = MagicMock()
    response.read = AsyncMock(return_value=content)
    return response


async def test_read_body_success():
    response = _mock_response(b"hello world")
    result = await read_body_with_max_size(response, max_size=1024)
    assert result == b"hello world"


async def test_read_body_no_max_size():
    response = _mock_response(b"hello world")
    result = await read_body_with_max_size(response, max_size=None)
    assert result == b"hello world"


async def test_read_body_exceeds_max_size():
    response = _mock_response(b"x" * 1024)
    with pytest.raises(BodyExceededMaxSize):
        await read_body_with_max_size(response, max_size=512)


async def test_read_body_exact_max_size():
    """Body exactly at max_size should succeed (> not >=)."""
    response = _mock_response(b"x" * 512)
    result = await read_body_with_max_size(response, max_size=512)
    assert len(result) == 512


async def test_read_body_one_over_max_size():
    response = _mock_response(b"x" * 513)
    with pytest.raises(BodyExceededMaxSize):
        await read_body_with_max_size(response, max_size=512)


async def test_read_empty_body():
    response = _mock_response(b"")
    result = await read_body_with_max_size(response, max_size=1024)
    assert result == b""
