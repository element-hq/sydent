# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.

from io import BytesIO
from unittest.mock import MagicMock

from twisted.internet import defer
from twisted.python.failure import Failure
from twisted.trial import unittest
from twisted.web.client import ResponseDone
from twisted.web.http import PotentialDataLoss
from twisted.web.iweb import UNKNOWN_LENGTH

from sydent.http.httpcommon import (
    BodyExceededMaxSize,
    SizeLimitingRequest,
    _DiscardBodyWithMaxSizeProtocol,
    _ReadBodyWithMaxSizeProtocol,
    read_body_with_max_size,
)


class ReadBodyWithMaxSizeProtocolTest(unittest.TestCase):
    """Tests for _ReadBodyWithMaxSizeProtocol."""

    def _make_protocol(
        self, max_size: int | None = None
    ) -> tuple[_ReadBodyWithMaxSizeProtocol, "defer.Deferred[bytes]"]:
        d: defer.Deferred[bytes] = defer.Deferred()
        protocol = _ReadBodyWithMaxSizeProtocol(d, max_size)
        protocol.transport = MagicMock()
        return protocol, d

    def test_reads_body_under_limit(self) -> None:
        """Body under the limit is read successfully."""
        protocol, d = self._make_protocol(max_size=100)
        protocol.dataReceived(b"hello ")
        protocol.dataReceived(b"world")
        protocol.connectionLost(Failure(ResponseDone()))
        self.assertEqual(self.successResultOf(d), b"hello world")

    def test_exceeds_max_size(self) -> None:
        """Body exceeding max_size triggers BodyExceededMaxSize."""
        protocol, d = self._make_protocol(max_size=5)
        protocol.dataReceived(b"too much data")
        self.failureResultOf(d, BodyExceededMaxSize)
        protocol.transport.abortConnection.assert_called_once()

    def test_exact_boundary(self) -> None:
        """Body exactly at max_size triggers the error (>= check)."""
        protocol, d = self._make_protocol(max_size=5)
        protocol.dataReceived(b"12345")
        self.failureResultOf(d, BodyExceededMaxSize)

    def test_no_max_size(self) -> None:
        """With max_size=None, any amount of data is accepted."""
        protocol, d = self._make_protocol(max_size=None)
        protocol.dataReceived(b"x" * 10000)
        protocol.connectionLost(Failure(ResponseDone()))
        self.assertEqual(len(self.successResultOf(d)), 10000)

    def test_potential_data_loss_succeeds(self) -> None:
        """PotentialDataLoss is treated as success (same as ResponseDone)."""
        protocol, d = self._make_protocol(max_size=1000)
        protocol.dataReceived(b"partial data")
        protocol.connectionLost(Failure(PotentialDataLoss()))
        self.assertEqual(self.successResultOf(d), b"partial data")

    def test_connection_error_propagates(self) -> None:
        """An unexpected connection loss reason propagates the error."""
        protocol, d = self._make_protocol(max_size=1000)
        protocol.dataReceived(b"some data")
        error = Failure(Exception("connection reset"))
        protocol.connectionLost(error)
        f = self.failureResultOf(d)
        self.assertIsInstance(f.value, Exception)


class DiscardBodyWithMaxSizeProtocolTest(unittest.TestCase):
    """Tests for _DiscardBodyWithMaxSizeProtocol."""

    def test_errors_on_data_received(self) -> None:
        """Fires errback and aborts connection on first data."""
        d: defer.Deferred[bytes] = defer.Deferred()
        protocol = _DiscardBodyWithMaxSizeProtocol(d)
        protocol.transport = MagicMock()
        protocol.dataReceived(b"any data")
        self.failureResultOf(d, BodyExceededMaxSize)
        protocol.transport.abortConnection.assert_called_once()

    def test_errors_on_connection_lost(self) -> None:
        """Also fires errback if connectionLost fires first."""
        d: defer.Deferred[bytes] = defer.Deferred()
        protocol = _DiscardBodyWithMaxSizeProtocol(d)
        protocol.transport = MagicMock()
        protocol.connectionLost(Failure(ResponseDone()))
        self.failureResultOf(d, BodyExceededMaxSize)

    def test_idempotent(self) -> None:
        """Multiple calls to _maybe_fail don't double-fire the deferred."""
        d: defer.Deferred[bytes] = defer.Deferred()
        protocol = _DiscardBodyWithMaxSizeProtocol(d)
        protocol.transport = MagicMock()
        protocol.dataReceived(b"first")
        protocol.dataReceived(b"second")  # should not raise
        protocol.connectionLost(Failure(ResponseDone()))
        self.failureResultOf(d, BodyExceededMaxSize)


class ReadBodyWithMaxSizeFunctionTest(unittest.TestCase):
    """Tests for the read_body_with_max_size() top-level function."""

    def _make_response(self, length: int | object = UNKNOWN_LENGTH) -> MagicMock:
        response = MagicMock()
        response.length = length
        return response

    def test_content_length_exceeds_limit_uses_discard(self) -> None:
        """When Content-Length > max_size, the Discard protocol is used."""
        response = self._make_response(length=200)
        read_body_with_max_size(response, max_size=100)
        response.deliverBody.assert_called_once()
        protocol = response.deliverBody.call_args[0][0]
        self.assertIsInstance(protocol, _DiscardBodyWithMaxSizeProtocol)

    def test_content_length_under_limit_uses_reader(self) -> None:
        """When Content-Length <= max_size, the Read protocol is used."""
        response = self._make_response(length=50)
        read_body_with_max_size(response, max_size=100)
        response.deliverBody.assert_called_once()
        protocol = response.deliverBody.call_args[0][0]
        self.assertIsInstance(protocol, _ReadBodyWithMaxSizeProtocol)

    def test_unknown_length_uses_reader(self) -> None:
        """When Content-Length is unknown, the Read protocol is used."""
        response = self._make_response(length=UNKNOWN_LENGTH)
        read_body_with_max_size(response, max_size=100)
        response.deliverBody.assert_called_once()
        protocol = response.deliverBody.call_args[0][0]
        self.assertIsInstance(protocol, _ReadBodyWithMaxSizeProtocol)

    def test_no_max_size_uses_reader(self) -> None:
        """When max_size is None, always uses the Read protocol."""
        response = self._make_response(length=999999)
        read_body_with_max_size(response, max_size=None)
        response.deliverBody.assert_called_once()
        protocol = response.deliverBody.call_args[0][0]
        self.assertIsInstance(protocol, _ReadBodyWithMaxSizeProtocol)


class SizeLimitingRequestTest(unittest.TestCase):
    """Tests for SizeLimitingRequest.handleContentChunk()."""

    def _make_request(self) -> SizeLimitingRequest:
        """Create a minimal SizeLimitingRequest for testing."""
        req = SizeLimitingRequest.__new__(SizeLimitingRequest)
        req.content = BytesIO()
        req.transport = MagicMock()
        # The client attribute is accessed for logging.
        req.client = MagicMock()
        return req

    def test_accepts_data_under_limit(self) -> None:
        """Chunks totalling under MAX_REQUEST_SIZE are accepted."""
        req = self._make_request()
        data = b"x" * 1000
        req.handleContentChunk(data)
        self.assertEqual(req.content.tell(), 1000)
        req.transport.abortConnection.assert_not_called()

    def test_aborts_on_oversize(self) -> None:
        """Connection is aborted when cumulative data exceeds MAX_REQUEST_SIZE."""
        req = self._make_request()
        # Write data right up to the limit.
        req.content.write(b"x" * (512 * 1024))
        req.content.seek(512 * 1024)
        # The next chunk pushes over.
        req.handleContentChunk(b"x")
        req.transport.abortConnection.assert_called_once()
