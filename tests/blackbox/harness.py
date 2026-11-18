# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
"""How to start and stop a Sydent process for the black-box tests.

The tests under `tests/blackbox` run Sydent as a subprocess and talk to it over
HTTP only. They pin Sydent's observable behaviour so that it stays the same
while the networking framework underneath is replaced. This module and
`_netshim.py` are the only files of `tests/blackbox` that such a change may
edit. The tests, `fakes.py`, `flows.py`, `environment.py` and `conftest.py`
must pass unchanged.

The contract between the tests and the harness:

- A test gets a `Sydent` from `Env.sydent(config, ...)`, where `config` maps
  INI section names to options. The harness adds the listeners, database,
  templates, pidfile and SMTP settings.
- The tests use only these members of `Sydent`: `request()`, `internal_url`,
  `replication_url`, `client_port`, `internal_port`, `db_path`, `directory`,
  `server_name`, `extra_config`, `proc` (a `subprocess.Popen`), `start()`,
  `stop()`, `restart()` and `log_tail()`.
- A test reads or writes the SQLite database directly for two things Sydent
  has no API for: adding replication peers, and ageing a validation session.
- `start()` returns once every listener accepts connections and
  `GET /_matrix/identity/v2` answers 200. `stop()` sends SIGTERM and waits for
  the exit code.
- The process sees only the virtual network described in `fakes.Net`:
  `_netshim.py` resolves host names from its table, redirects listed
  addresses to local fakes, refuses any other non-loopback destination and
  sends SRV lookups to the fake DNS server. `SSL_CERT_FILE` and
  `SSL_CERT_DIR` make the test CA the only trust root from the platform.

Nothing here imports Sydent: the process is configured through its config
file and environment only.
"""

from __future__ import annotations

import configparser
import os
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from tests.blackbox.fakes import Net, Response, http_request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NETSHIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_netshim.py")
# Arbitrary, well above the second or so that Sydent takes to start, so that a
# loaded CI machine does not fail the run.
STARTUP_TIMEOUT = 30.0
# Arbitrary. A process still running after this is killed.
STOP_TIMEOUT = 10.0

Config = Dict[str, Dict[str, str]]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class SydentStartError(Exception):
    pass


class Sydent:
    """One Sydent process with its own directory, database and ports."""

    def __init__(
        self,
        directory: str,
        net: Net,
        ca_cert_path: str,
        config: Config,
        server_name: str,
        replication_cert: Optional[str] = None,
        internal_api: bool = False,
    ) -> None:
        self.directory = directory
        self.net = net
        self.ca_cert_path = ca_cert_path
        self.server_name = server_name
        self.replication_cert = replication_cert
        self.internal_api = internal_api
        self.extra_config = config
        self.client_port = free_port()
        self.replication_port = free_port()
        self.internal_port: Optional[int] = free_port() if internal_api else None
        self.config_path = os.path.join(directory, "sydent.conf")
        self.db_path = os.path.join(directory, "sydent.db")
        self.log_path = os.path.join(directory, "stderr.log")
        self.proc: Optional[subprocess.Popen[bytes]] = None
        self.returncode: Optional[int] = None
        self.starts = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.client_port}"

    @property
    def internal_url(self) -> str:
        return f"http://127.0.0.1:{self.internal_port}"

    @property
    def replication_url(self) -> str:
        return f"https://127.0.0.1:{self.replication_port}"

    def request(self, method: str, path: str, **kwargs: Any) -> Response:
        return http_request(self.base_url, method, path, **kwargs)

    def _write_config(self) -> None:
        cfg = configparser.ConfigParser()
        cfg.optionxform = str  # type: ignore[assignment,method-assign]
        base: Config = {
            "general": {
                "server.name": self.server_name,
                "templates.path": os.path.join(REPO_ROOT, "res"),
                "pidfile.path": os.path.join(self.directory, "sydent.pid"),
                "log.level": "INFO",
            },
            "db": {"db.file": self.db_path},
            "http": {
                "clientapi.http.bind_address": "127.0.0.1",
                "clientapi.http.port": str(self.client_port),
                "replication.https.bind_address": "127.0.0.1",
                "replication.https.port": str(self.replication_port),
                "client_http_base": f"http://{self.server_name}",
            },
            "email": {"email.smtphost": "127.0.0.1", "email.smtpport": "1"},
        }
        if self.internal_port is not None:
            base["http"]["internalapi.http.bind_address"] = "127.0.0.1"
            base["http"]["internalapi.http.port"] = str(self.internal_port)
        if self.replication_cert is not None:
            base["http"]["replication.https.certfile"] = self.replication_cert
        for section, values in self.extra_config.items():
            base.setdefault(section, {}).update(values)
        if os.path.exists(self.config_path):
            # Keep the signing key Sydent generated on its first start.
            existing = configparser.ConfigParser()
            existing.optionxform = str  # type: ignore[assignment,method-assign]
            existing.read(self.config_path)
            if existing.has_option("crypto", "ed25519.signingkey"):
                base.setdefault("crypto", {})["ed25519.signingkey"] = existing.get(
                    "crypto", "ed25519.signingkey"
                )
        for section, values in base.items():
            cfg[section] = values
        with open(self.config_path, "w") as f:
            cfg.write(f)

    def _env(self) -> Dict[str, str]:
        env = {
            k: v for k, v in os.environ.items() if not k.startswith(("SYDENT_", "SSL_"))
        }
        empty_dir = os.path.join(self.directory, "no-certs")
        os.makedirs(empty_dir, exist_ok=True)
        env.update(
            {
                "SYDENT_CONF": self.config_path,
                "SYDENT_TEST_NET": self.net.path,
                # The only trust root for outbound TLS that uses the platform store.
                "SSL_CERT_FILE": self.ca_cert_path,
                "SSL_CERT_DIR": empty_dir,
                "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": REPO_ROOT,
            }
        )
        return env

    def start(self) -> None:
        last_error = ""
        for _attempt in range(3):
            try:
                self._start_once()
                return
            except SydentStartError as e:
                last_error = str(e)
                if "already in use" not in last_error.lower():
                    raise
                # Another process took a port between free_port() and bind().
                self.client_port = free_port()
                self.replication_port = free_port()
                if self.internal_port is not None:
                    self.internal_port = free_port()
        raise SydentStartError(last_error)

    def _start_once(self) -> None:
        self._write_config()
        self.starts += 1
        log = open(self.log_path, "ab")
        log.write(f"\n===== start #{self.starts} =====\n".encode())
        log.flush()
        self.proc = subprocess.Popen(
            [sys.executable, NETSHIM],
            cwd=self.directory,
            env=self._env(),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.close()
        self.returncode = None
        deadline = time.monotonic() + STARTUP_TIMEOUT
        ports = [self.client_port]
        if self.internal_port is not None:
            ports.append(self.internal_port)
        if self.replication_cert is not None:
            ports.append(self.replication_port)
        while True:
            if self.proc.poll() is not None:
                self.returncode = self.proc.returncode
                raise SydentStartError(
                    f"Sydent exited with {self.proc.returncode} during startup:\n"
                    + self.log_tail()
                )
            if all(self._accepts(p) for p in ports) and self._answers():
                return
            if time.monotonic() > deadline:
                self.kill()
                raise SydentStartError(
                    "Sydent did not become ready:\n" + self.log_tail()
                )
            time.sleep(0.02)

    @staticmethod
    def _accepts(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def _answers(self) -> bool:
        try:
            return self.request("GET", "/_matrix/identity/v2", timeout=2).status == 200
        except OSError:
            return False

    def stop(self, sig: int = signal.SIGTERM) -> Optional[int]:
        """Signal the process and wait for it to exit; returns its exit code."""
        if self.proc is None or self.proc.poll() is not None:
            return self.returncode if self.proc is None else self.proc.returncode
        self.proc.send_signal(sig)
        try:
            self.returncode = self.proc.wait(STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.kill()
        return self.returncode

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.returncode = self.proc.wait()

    def restart(self) -> None:
        self.stop()
        self.start()

    def log_tail(self, max_bytes: int = 20000) -> str:
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return "<no log>"
