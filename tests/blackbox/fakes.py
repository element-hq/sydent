# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Framework-independent fake peers for the black-box tests.

Everything here uses the standard library (plus `cryptography` for the test CA)
and runs in background threads of the test process, so none of it changes when
Sydent moves between networking frameworks.

`tests/utils.py` drives Sydent in-process on Twisted's memory reactor, and the
integration suite's fake homeserver and mail sink run in child processes on
fixed ports. Neither fits here: these fakes bind port 0, so test modules can
run in parallel, and they record what they received for the tests to inspect.
"""

from __future__ import annotations

import datetime
import http.client
import ipaddress
import json
import os
import select
import socket
import socketserver
import ssl
import struct
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple, Union

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# Arbitrary, well above the time Sydent takes to send a mail or a request.
WAIT_TIMEOUT = 10.0


def wait_until(
    predicate: Callable[[], Any], timeout: float = WAIT_TIMEOUT, interval: float = 0.02
) -> Any:
    """Poll `predicate` until it returns a truthy value, or fail after `timeout`."""
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result:
            return result
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout}s: {predicate}")
        time.sleep(interval)


def ipv6_available() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# Test CA
# --------------------------------------------------------------------------


class TestCA:
    """A throwaway certificate authority writing PEM files into `directory`."""

    __test__ = False

    def __init__(self, directory: str, name: str = "Sydent black-box test CA") -> None:
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self._key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        now = datetime.datetime.now(datetime.timezone.utc)
        self._cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(self._key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(self._key.public_key()),
                False,
            )
            .sign(self._key, hashes.SHA256())
        )
        self.cert_path = os.path.join(directory, "ca.pem")
        with open(self.cert_path, "wb") as f:
            f.write(self._cert.public_bytes(serialization.Encoding.PEM))
        self._count = 0

    def issue(
        self,
        common_name: str,
        dns_names: Iterable[str] = (),
        ip_addresses: Iterable[str] = (),
    ) -> str:
        """Issue a leaf certificate; returns the path of a PEM file holding the
        private key followed by the certificate."""
        self._count += 1
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.datetime.now(datetime.timezone.utc)
        sans: List[x509.GeneralName] = [x509.DNSName(n) for n in dns_names]
        sans += [x509.IPAddress(ipaddress.ip_address(a)) for a in ip_addresses]
        builder = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
            )
            .issuer_name(self._cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(
                x509.ExtendedKeyUsage(
                    [
                        x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                        x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
                    ]
                ),
                False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    self._key.public_key()
                ),
                False,
            )
        )
        if sans:
            builder = builder.add_extension(x509.SubjectAlternativeName(sans), False)
        cert = builder.sign(self._key, hashes.SHA256())
        path = os.path.join(self.directory, f"leaf{self._count}-{common_name}.pem")
        with open(path, "wb") as f:
            f.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        return path

    def server_context(self, cert_path: str) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path)
        return ctx

    def client_context(self, cert_path: Optional[str] = None) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(self.cert_path)
        if cert_path is not None:
            ctx.load_cert_chain(cert_path)
        return ctx


# --------------------------------------------------------------------------
# HTTP client used to talk to Sydent
# --------------------------------------------------------------------------


@dataclass
class Response:
    status: int
    reason: str
    headers: List[Tuple[str, str]]
    body: bytes

    def header(self, name: str) -> Optional[str]:
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return None

    def json(self) -> Any:
        return json.loads(self.body)

    def __repr__(self) -> str:
        return f"<Response {self.status} {self.body[:300]!r}>"


_NO_JSON = object()


def http_request(
    base_url: str,
    method: str,
    path: str,
    json_body: Any = _NO_JSON,
    body: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> Response:
    """Send one request on a fresh connection. `path` is sent verbatim."""
    url = urllib.parse.urlsplit(base_url)
    assert url.hostname is not None and url.port is not None
    conn: http.client.HTTPConnection
    if url.scheme == "https":
        conn = http.client.HTTPSConnection(
            url.hostname, url.port, timeout=timeout, context=ssl_context
        )
    else:
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
    hdrs = dict(headers or {})
    if json_body is not _NO_JSON:
        body = json.dumps(json_body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    try:
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        return Response(resp.status, resp.reason, resp.getheaders(), data)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Fake HTTP(S) server
# --------------------------------------------------------------------------


@dataclass
class Conn:
    family: int
    local_port: int
    sni: Optional[str] = None
    tls_error: Optional[str] = None


@dataclass
class Request:
    method: str
    path: str
    headers: Dict[str, str]
    body: bytes
    conn: Conn

    @property
    def query(self) -> Dict[str, List[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass
class Reply:
    status: int = 200
    body: Union[bytes, Iterator[bytes]] = b""
    headers: Dict[str, str] = field(default_factory=dict)
    # When `body` is an iterator: send a Content-Length of this value (or none).
    content_length: Optional[int] = None


def json_reply(obj: Any, status: int = 200) -> Reply:
    return Reply(status, json.dumps(obj).encode(), {"Content-Type": "application/json"})


Handler = Callable[[Request], Reply]


@dataclass
class StreamStats:
    bytes_sent: int = 0
    closed_by_peer: bool = False
    finished: bool = False


class FakeHTTPServer:
    """A threaded HTTP/1.1 server listening on 127.0.0.1 and, when available, ::1.

    Each port gets its own ephemeral port number. TLS is optional; when enabled
    the SNI sent by the client and any handshake failure are recorded."""

    def __init__(
        self, tls_cert: Optional[str] = None, ca: Optional[TestCA] = None
    ) -> None:
        self.routes: Dict[Tuple[str, str], Handler] = {}
        self.requests: List[Request] = []
        self.connections: List[Conn] = []
        self.streams: List[StreamStats] = []
        self._lock = threading.Lock()
        self._ctx: Optional[ssl.SSLContext] = None
        if tls_cert is not None:
            assert ca is not None
            ctx = ca.server_context(tls_cert)

            def sni_cb(sock: Any, name: Optional[str], _ctx: Any) -> None:
                sock.sni_name = name

            ctx.sni_callback = sni_cb
            self._ctx = ctx
        self._socks: List[socket.socket] = []
        self.port4 = self._listen(socket.AF_INET, "127.0.0.1")
        self.port6 = self._listen(socket.AF_INET6, "::1") if ipv6_available() else None
        self._stopped = threading.Event()
        for s in self._socks:
            threading.Thread(target=self._accept_loop, args=(s,), daemon=True).start()

    @property
    def scheme(self) -> str:
        return "https" if self._ctx else "http"

    def _listen(self, family: int, host: str) -> int:
        s = socket.socket(family, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, 0))
        s.listen(64)
        self._socks.append(s)
        port: int = s.getsockname()[1]
        return port

    def route(self, method: str, path: str, handler: Union[Handler, Reply]) -> None:
        """Register a handler for an exact path (query string excluded)."""
        if isinstance(handler, Reply):
            reply = handler
            self.routes[(method, path)] = lambda _req: reply
        else:
            self.routes[(method, path)] = handler

    def requests_to(self, path: str) -> List[Request]:
        with self._lock:
            return [r for r in self.requests if r.path.split("?")[0] == path]

    def _accept_loop(self, lsock: socket.socket) -> None:
        while not self._stopped.is_set():
            try:
                r, _, _ = select.select([lsock], [], [], 0.2)
                if not r:
                    continue
                conn, addr = lsock.accept()
            except OSError:
                return
            threading.Thread(
                target=self._serve, args=(conn, addr, lsock), daemon=True
            ).start()

    def _serve(self, raw: socket.socket, addr: Any, lsock: socket.socket) -> None:
        record = Conn(family=lsock.family, local_port=lsock.getsockname()[1])
        with self._lock:
            self.connections.append(record)
        sock: socket.socket = raw
        raw.settimeout(15)
        if self._ctx is not None:
            try:
                tls_sock = self._ctx.wrap_socket(raw, server_side=True)
            except (ssl.SSLError, OSError) as e:
                record.tls_error = repr(e)
                raw.close()
                return
            record.sni = getattr(tls_sock, "sni_name", None)
            sock = tls_sock
        try:
            _RequestHandler(sock, addr, self, record)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stopped.set()
        for s in self._socks:
            s.close()


class _RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(
        self, sock: socket.socket, addr: Any, fake: FakeHTTPServer, conn: Conn
    ) -> None:
        self.fake = fake
        self.conn_record = conn
        super().__init__(sock, addr, None)  # type: ignore[arg-type]

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _handle_any(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        req = Request(
            method=self.command,
            path=self.path,
            headers={k.lower(): v for k, v in self.headers.items()},
            body=body,
            conn=self.conn_record,
        )
        with self.fake._lock:
            self.fake.requests.append(req)
        handler = self.fake.routes.get((self.command, self.path.split("?")[0]))
        reply = handler(req) if handler else Reply(404, b'{"errcode":"M_NOT_FOUND"}')
        self.close_connection = True
        self.send_response(reply.status)
        for k, v in reply.headers.items():
            self.send_header(k, v)
        self.send_header("Connection", "close")
        if isinstance(reply.body, bytes):
            self.send_header("Content-Length", str(len(reply.body)))
            self.end_headers()
            self.wfile.write(reply.body)
            return
        if reply.content_length is not None:
            self.send_header("Content-Length", str(reply.content_length))
        self.end_headers()
        stats = StreamStats()
        with self.fake._lock:
            self.fake.streams.append(stats)
        try:
            for chunk in reply.body:
                self.wfile.write(chunk)
                self.wfile.flush()
                stats.bytes_sent += len(chunk)
            stats.finished = True
        except OSError:
            stats.closed_by_peer = True

    do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = _handle_any


def stream_of(
    total: int,
    chunk: int = 64 * 1024,
    pause_after: Optional[int] = None,
    gate: Optional[threading.Event] = None,
) -> Iterator[bytes]:
    """`total` bytes of JSON-ish filler, produced lazily.

    With `pause_after` and `gate`, stops after that many bytes until the gate is
    set (or WAIT_TIMEOUT passes)."""
    yield b'{"sub": "'
    sent = 9
    block = b"a" * chunk
    while sent < total:
        if pause_after is not None and gate is not None and sent >= pause_after:
            gate.wait(WAIT_TIMEOUT)
            pause_after = None
        n = min(chunk, total - sent)
        yield block[:n]
        sent += n


# --------------------------------------------------------------------------
# Fake DNS server (UDP)
# --------------------------------------------------------------------------

QTYPE_A = 1
QTYPE_AAAA = 28
QTYPE_SRV = 33
RCODE_NXDOMAIN = 3
RCODE_SERVFAIL = 2


@dataclass(frozen=True)
class SRV:
    target: str
    port: int
    priority: int = 0
    weight: int = 0
    ttl: int = 300


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        if label:
            out += bytes([len(label)]) + label.encode("ascii")
    return out + b"\x00"


def _decode_name(packet: bytes, offset: int) -> Tuple[str, int]:
    labels: List[str] = []
    while True:
        length = packet[offset]
        if length == 0:
            return ".".join(labels), offset + 1
        labels.append(packet[offset + 1 : offset + 1 + length].decode("ascii"))
        offset += 1 + length


class FakeDNSServer:
    """Answers SRV, A and AAAA queries from in-memory tables.

    Names with no entry at all get NXDOMAIN; names listed in `nodata` exist but
    have no records of the asked type (NOERROR with an empty answer)."""

    def __init__(self) -> None:
        self.srv: Dict[str, List[SRV]] = {}
        self.addresses: Dict[str, List[str]] = {}
        self.nodata: set[str] = set()
        self.servfail: set[str] = set()
        self.queries: List[Tuple[str, int]] = []
        self._lock = threading.Lock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.address: Tuple[str, int] = self.sock.getsockname()
        self._stopped = False
        threading.Thread(target=self._loop, daemon=True).start()

    def queried(self, qtype: int = QTYPE_SRV) -> List[str]:
        with self._lock:
            return [n for n, t in self.queries if t == qtype]

    def _loop(self) -> None:
        while not self._stopped:
            try:
                r, _, _ = select.select([self.sock], [], [], 0.2)
                if not r:
                    continue
                packet, peer = self.sock.recvfrom(4096)
            except OSError:
                return
            try:
                self.sock.sendto(self._answer(packet), peer)
            except Exception:
                continue

    def _answer(self, packet: bytes) -> bytes:
        (qid, flags, qdcount) = struct.unpack("!HHH", packet[:6])
        name, offset = _decode_name(packet, 12)
        qtype, qclass = struct.unpack("!HH", packet[offset : offset + 4])
        question = packet[12 : offset + 4]
        key = name.lower().rstrip(".")
        with self._lock:
            self.queries.append((key, qtype))
        answers: List[bytes] = []
        rcode = 0
        if key in self.servfail:
            rcode = RCODE_SERVFAIL
        elif qtype == QTYPE_SRV and key in self.srv:
            for rec in self.srv[key]:
                rdata = struct.pack("!HHH", rec.priority, rec.weight, rec.port)
                rdata += _encode_name(rec.target)
                answers.append(
                    b"\xc0\x0c"
                    + struct.pack("!HHIH", QTYPE_SRV, 1, rec.ttl, len(rdata))
                    + rdata
                )
        elif qtype in (QTYPE_A, QTYPE_AAAA) and key in self.addresses:
            for addr in self.addresses[key]:
                ip = ipaddress.ip_address(addr)
                if (ip.version == 4) != (qtype == QTYPE_A):
                    continue
                answers.append(
                    b"\xc0\x0c"
                    + struct.pack("!HHIH", qtype, 1, 300, len(ip.packed))
                    + ip.packed
                )
        elif key in self.nodata or key in self.srv or key in self.addresses:
            pass
        else:
            rcode = RCODE_NXDOMAIN
        rflags = 0x8000 | (flags & 0x0100) | 0x0080 | 0x0400 | rcode
        header = struct.pack("!HHHHHH", qid, rflags, 1, len(answers), 0, 0)
        return header + question + b"".join(answers)

    def stop(self) -> None:
        self._stopped = True
        self.sock.close()


# --------------------------------------------------------------------------
# SMTP sink
# --------------------------------------------------------------------------


@dataclass
class Mail:
    mail_from: str
    rcpt_to: List[str]
    data: bytes

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


class SMTPSink:
    """A minimal SMTP server that accepts every message.

    The listening socket is bound before the constructor returns, so the sink is
    ready as soon as it exists."""

    def __init__(self) -> None:
        self.messages: List[Mail] = []
        self._lock = threading.Lock()
        sink = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                self.request.settimeout(15)
                self.wfile.write(b"220 sink ESMTP\r\n")
                mail_from = ""
                rcpt: List[str] = []
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    cmd = line.decode("ascii", errors="replace").strip()
                    upper = cmd.upper()
                    if upper.startswith(("EHLO", "HELO")):
                        self.wfile.write(b"250 sink\r\n")
                    elif upper.startswith("MAIL FROM:"):
                        mail_from = cmd[10:].strip().strip("<>")
                        rcpt = []
                        self.wfile.write(b"250 OK\r\n")
                    elif upper.startswith("RCPT TO:"):
                        rcpt.append(cmd[8:].strip().strip("<>"))
                        self.wfile.write(b"250 OK\r\n")
                    elif upper == "DATA":
                        self.wfile.write(b"354 go ahead\r\n")
                        chunks = []
                        while True:
                            dl = self.rfile.readline()
                            if not dl or dl == b".\r\n":
                                break
                            if dl.startswith(b".."):
                                dl = dl[1:]
                            chunks.append(dl)
                        with sink._lock:
                            sink.messages.append(
                                Mail(mail_from, rcpt, b"".join(chunks))
                            )
                        self.wfile.write(b"250 OK queued\r\n")
                    elif upper == "QUIT":
                        self.wfile.write(b"221 bye\r\n")
                        return
                    else:
                        self.wfile.write(b"250 OK\r\n")

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port: int = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def wait_for(self, count: int = 1, timeout: float = WAIT_TIMEOUT) -> List[Mail]:
        wait_until(lambda: len(self.messages) >= count, timeout)
        with self._lock:
            return list(self.messages)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# --------------------------------------------------------------------------
# Virtual network seen by a Sydent process
# --------------------------------------------------------------------------


class Net:
    """The view of the network given to one Sydent process.

    Host names resolve only through `hosts` (mirrored into the fake DNS server).
    Connections to a (virtual address, port) listed in the redirects go to a
    local fake; any other non-loopback destination is refused. The table is
    written to `path` and re-read by the process on every lookup."""

    V4_PREFIX = "198.51.100."
    V6_PREFIX = "2001:db8::"

    def __init__(self, path: str, dns: FakeDNSServer) -> None:
        self.path = path
        self.dns = dns
        self.hosts: Dict[str, List[str]] = {}
        self.redirects: Dict[str, Tuple[str, int]] = {}
        self._next = 10
        self._write()

    def new_address(self, v6: bool = False) -> str:
        self._next += 1
        prefix = self.V6_PREFIX if v6 else self.V4_PREFIX
        return f"{prefix}{self._next}"

    def add_host(self, name: str, *addresses: str) -> None:
        self.hosts[name.lower()] = list(addresses)
        self.dns.addresses[name.lower()] = list(addresses)
        self._write()

    def serve(self, address: str, port: int, fake: FakeHTTPServer) -> None:
        """Make `address:port` reach `fake`."""
        ip = ipaddress.ip_address(address)
        if ip.version == 6:
            assert fake.port6 is not None, "IPv6 loopback unavailable"
            target = ("::1", fake.port6)
        else:
            target = ("127.0.0.1", fake.port4)
        self.redirects[f"{ip.compressed}|{port}"] = target
        self._write()

    def _write(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(
                {
                    "hosts": self.hosts,
                    "redirects": self.redirects,
                    "dns": list(self.dns.address),
                },
                f,
            )
        os.replace(tmp, self.path)
