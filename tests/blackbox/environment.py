# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""A test's (or a module's) fakes and Sydent processes."""

from __future__ import annotations

import os
from typing import List, Optional

import pytest

from tests.blackbox.fakes import FakeDNSServer, FakeHTTPServer, Net, SMTPSink, TestCA
from tests.blackbox.harness import Config, Sydent

# Fakes live on these virtual ranges. Both are in Sydent's default IP blacklist,
# so every Sydent whitelists them unless a test overrides `ip.whitelist`.
VIRTUAL_RANGES = "198.51.100.0/24, 2001:db8::/32"

SYDENTS = pytest.StashKey[List[Sydent]]()


class Env:
    """Everything one test (or one module) needs to run Sydents against fakes."""

    def __init__(self, directory: str, ca: TestCA, node: pytest.Item) -> None:
        self.directory = directory
        self.ca = ca
        self.node = node
        self.dns = FakeDNSServer()
        self.net = Net(os.path.join(directory, "net.json"), self.dns)
        self.smtp = SMTPSink()
        self._fakes: List[FakeHTTPServer] = []
        self.sydents: List[Sydent] = []
        node.stash[SYDENTS] = self.sydents

    def fake(
        self,
        cert_names: Optional[List[str]] = None,
        tls: bool = True,
        cert_ips: Optional[List[str]] = None,
    ) -> FakeHTTPServer:
        """A fake HTTP server; with TLS, its certificate (from the test CA) is
        valid for `cert_names` and `cert_ips`."""
        cert = None
        if tls:
            names = cert_names or ["fake.test"]
            cert = self.ca.issue(names[0], dns_names=names, ip_addresses=cert_ips or [])
        server = FakeHTTPServer(cert, self.ca)
        self._fakes.append(server)
        return server

    def host(
        self, name: str, fake: FakeHTTPServer, *ports: int, v6: bool = False
    ) -> str:
        """Give `name` a fresh virtual address and route `ports` on it to `fake`."""
        address = self.net.new_address(v6=v6)
        self.net.add_host(name, address)
        for port in ports:
            self.net.serve(address, port, fake)
        return address

    def sydent(
        self,
        config: Optional[Config] = None,
        server_name: Optional[str] = None,
        replication_cert: Optional[str] = None,
        internal_api: bool = False,
        start: bool = True,
    ) -> Sydent:
        index = len(self.sydents)
        name = server_name or f"sydent{index}.test"
        directory = os.path.join(self.directory, f"sydent{index}")
        os.makedirs(directory)
        merged: Config = {
            "general": {"ip.whitelist": VIRTUAL_RANGES},
            "email": {"email.smtpport": str(self.smtp.port)},
        }
        for section, values in (config or {}).items():
            merged.setdefault(section, {}).update(values)
        s = Sydent(
            directory,
            self.net,
            self.ca.cert_path,
            merged,
            name,
            replication_cert=replication_cert,
            internal_api=internal_api,
        )
        self.sydents.append(s)
        if start:
            s.start()
        return s

    def close(self) -> None:
        for s in self.sydents:
            s.stop()
        for f in self._fakes:
            f.stop()
        self.dns.stop()
        self.smtp.stop()
