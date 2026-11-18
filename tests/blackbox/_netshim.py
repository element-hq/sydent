# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""Entry point of the Sydent process under test, part of the harness.

It confines the process to the virtual network described by the JSON file in
$SYDENT_TEST_NET (see `fakes.Net` and the contract in `harness.py`), then runs
`sydent.sydent` as `__main__`:

- `socket.getaddrinfo` resolves host names from the table only.
- A TCP connection to a listed (address, port) goes to the matching local
  fake. Connections to other non-loopback destinations, and UDP datagrams to
  non-loopback addresses, are refused.
- SRV lookups go to the fake DNS server.

The hooks are installed before Sydent is imported, because a resolver may keep
a reference to `socket.getaddrinfo` from import time. The socket-level hooks
work for any framework built on the standard library's `socket` module. A
framework that bypasses it, such as uvloop or c-ares, needs its own hook. The
SRV hook is specific to the DNS library Sydent uses (`twisted.names`).
"""

import errno
import ipaddress
import json
import os
import runpy
import signal
import socket
from typing import Any, Optional, Tuple

_NET_FILE = os.environ["SYDENT_TEST_NET"]
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_sendto = socket.socket.sendto


def _net() -> Any:
    with open(_NET_FILE) as f:
        return json.load(f)


def _parse_ip(host: Any) -> Optional[Any]:
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if not isinstance(host, str):
        return None
    try:
        return ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return None


def _getaddrinfo(
    host: Any, port: Any, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0
) -> Any:
    if host is None or _parse_ip(host) is not None:
        return _real_getaddrinfo(host, port, family, type, proto, flags)
    name = host.decode("ascii") if isinstance(host, bytes) else str(host)
    addresses = _net()["hosts"].get(name.lower().rstrip("."), [])
    results = []
    for address in addresses:
        try:
            results += _real_getaddrinfo(
                address, port, family, type, proto, flags | socket.AI_NUMERICHOST
            )
        except socket.gaierror:
            pass
    if not results:
        raise socket.gaierror(socket.EAI_NONAME, f"{name}: not in the test network")
    return results


def _is_loopback(ip: Any) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(ip.is_loopback or (mapped is not None and mapped.is_loopback))


def _route(sock: socket.socket, address: Any) -> Tuple[Any, bool]:
    """Returns (address to use, allowed)."""
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return address, True
    ip = _parse_ip(address[0])
    if ip is None:
        return address, False
    target = _net()["redirects"].get(f"{ip.compressed}|{address[1]}")
    if target is not None:
        return (target[0], target[1]) + tuple(address[2:]), True
    return address, _is_loopback(ip)


def _connect(self: socket.socket, address: Any) -> None:
    address, allowed = _route(self, address)
    if not allowed:
        raise ConnectionRefusedError(errno.ECONNREFUSED, "outside the test network")
    _real_connect(self, address)


def _connect_ex(self: socket.socket, address: Any) -> int:
    address, allowed = _route(self, address)
    if not allowed:
        return errno.ECONNREFUSED
    return _real_connect_ex(self, address)


def _sendto(self: socket.socket, data: Any, *args: Any) -> int:
    address = args[-1]
    if self.family in (socket.AF_INET, socket.AF_INET6):
        ip = _parse_ip(address[0])
        if ip is None or not _is_loopback(ip):
            raise OSError(errno.ENETUNREACH, "outside the test network")
    return _real_sendto(self, data, *args)


socket.getaddrinfo = _getaddrinfo
socket.socket.connect = _connect  # type: ignore[method-assign,assignment]
socket.socket.connect_ex = _connect_ex  # type: ignore[method-assign,assignment]
socket.socket.sendto = _sendto  # type: ignore[method-assign,assignment]


def _install_srv_hook() -> None:
    import twisted.names.client

    host, port = _net()["dns"]
    resolver = None

    def get_resolver() -> Any:
        nonlocal resolver
        if resolver is None:
            resolver = twisted.names.client.Resolver(servers=[(host, port)])  # type: ignore[no-untyped-call]
        return resolver

    twisted.names.client.getResolver = get_resolver


_install_srv_hook()
# A test run started as a background job of a non-interactive shell passes on
# an ignored SIGINT, which Twisted then leaves alone. Restoring the default
# handler makes Sydent behave as when it is started from a terminal.
signal.signal(signal.SIGINT, signal.default_int_handler)
runpy.run_module("sydent.sydent", run_name="__main__", alter_sys=True)
