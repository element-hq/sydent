# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
# Copyright 2018 New Vector Ltd
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import asyncio
import gc
import logging
import logging.handlers
import os
import sqlite3

import attr
import prometheus_client
from aiohttp import web
from matrix_common.versionstring import get_distribution_version_string
from signedjson.types import SigningKey

from sydent.config import SydentConfig
from sydent.db.hashing_metadata import HashingMetadataStore
from sydent.db.sqlitedb import SqliteDatabase
from sydent.db.valsession import ThreePidValSessionStore
from sydent.hs_federation.verifier import Verifier
from sydent.http.httpcommon import SslComponents
from sydent.http.httpsclient import ReplicationHttpsClient
from sydent.http.httpserver import (
    ClientApiHttpServer,
    InternalApiHttpServer,
    ReplicationHttpsServer,
)
from sydent.replication.pusher import Pusher
from sydent.threepid.bind import ThreepidBinder
from sydent.util.hash import sha256_and_url_safe_base64
from sydent.util.ratelimiter import Ratelimiter
from sydent.util.tokenutils import generateAlphanumericTokenOfLength
from sydent.validators.emailvalidator import EmailValidator
from sydent.validators.msisdnvalidator import MsisdnValidator

logger = logging.getLogger(__name__)


class Sydent:
    def __init__(
        self,
        sydent_config: SydentConfig,
        use_tls_for_federation: bool = True,
    ):
        self.config = sydent_config
        self.use_tls_for_federation = use_tls_for_federation

        logger.info("Starting Sydent server")

        self.db: sqlite3.Connection = SqliteDatabase(self).db

        if self.config.general.sentry_enabled:
            import sentry_sdk

            sentry_sdk.init(
                dsn=self.config.general.sentry_dsn,
                release=get_distribution_version_string("matrix-sydent"),
            )
            with sentry_sdk.configure_scope() as scope:
                scope.set_tag("sydent_server_name", self.config.general.server_name)

            # workaround for https://github.com/getsentry/sentry-python/issues/803: we
            # disable automatic GC and run it periodically instead.
            gc.disable()
            self._gc_enabled = True
        else:
            self._gc_enabled = False

        # See if a pepper already exists in the database
        # Note: This MUST be run before we start serving requests, otherwise lookups for
        # 3PID hashes may come in before we've completed generating them
        hashing_metadata_store = HashingMetadataStore(self)
        lookup_pepper = hashing_metadata_store.get_lookup_pepper()
        if not lookup_pepper:
            lookup_pepper = generateAlphanumericTokenOfLength(5)
            hashing_metadata_store.store_lookup_pepper(
                sha256_and_url_safe_base64, lookup_pepper
            )

        self.validators: Validators = Validators(
            EmailValidator(self), MsisdnValidator(self)
        )

        self.keyring: Keyring = Keyring(self.config.crypto.signing_key)
        self.keyring.ed25519.alg = "ed25519"

        self.sig_verifier: Verifier = Verifier(self)

        self.threepidBinder: ThreepidBinder = ThreepidBinder(self)

        self.sslComponents: SslComponents = SslComponents(self)

        self.clientApiHttpServer = ClientApiHttpServer(self, lookup_pepper)
        self.replicationHttpsServer = ReplicationHttpsServer(self)
        self.replicationHttpsClient: ReplicationHttpsClient = ReplicationHttpsClient(
            self
        )

        self.pusher: Pusher = Pusher(self)

        self.email_sender_ratelimiter: Ratelimiter[str] = Ratelimiter(
            burst=self.config.email.email_sender_ratelimit_burst,
            rate_hz=self.config.email.email_sender_ratelimit_rate_hz,
        )

    async def run(self) -> None:
        """Start the Sydent server. This is the main async entry point."""
        runners: list[web.AppRunner] = []

        # Start HTTP servers
        runner = await self.clientApiHttpServer.start()
        runners.append(runner)

        repl_runner = await self.replicationHttpsServer.start()
        if repl_runner is not None:
            runners.append(repl_runner)

        # Start replication client and pusher
        await self.replicationHttpsClient.start()
        await self.pusher.start()

        # Start rate limiter
        await self.email_sender_ratelimiter.start()

        self.maybe_start_prometheus_server()

        # Start periodic tasks
        tasks: list[asyncio.Task[None]] = []

        # Session cleanup every 10 minutes
        cleanup_store = ThreePidValSessionStore(self)
        tasks.append(asyncio.create_task(self._periodic_cleanup(cleanup_store)))

        # GC task if sentry enabled
        if self._gc_enabled:
            tasks.append(asyncio.create_task(self._periodic_gc()))

        # Internal API server (optional)
        if self.config.http.internal_port is not None:
            internal_server = InternalApiHttpServer(self)
            internal_runner = await internal_server.start(
                self.config.http.internal_bind_address,
                self.config.http.internal_port,
            )
            runners.append(internal_runner)

        # Write PID file
        if self.config.general.pidfile:
            with open(self.config.general.pidfile, "w") as pidfile:
                pidfile.write(str(os.getpid()) + "\n")

        # Wait forever (until cancelled)
        try:
            await asyncio.Event().wait()
        finally:
            # Cleanup
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            await self.email_sender_ratelimiter.stop()
            await self.pusher.stop()
            await self.replicationHttpsClient.stop()

            for runner in runners:
                await runner.cleanup()

    def maybe_start_prometheus_server(self) -> None:
        if self.config.general.prometheus_enabled:
            assert self.config.general.prometheus_addr is not None
            assert self.config.general.prometheus_port is not None
            prometheus_client.start_http_server(
                port=self.config.general.prometheus_port,
                addr=self.config.general.prometheus_addr,
            )

    def ip_from_request(self, request: web.Request) -> str | None:
        if (
            self.config.http.obey_x_forwarded_for
            and "X-Forwarded-For" in request.headers
        ):
            return request.headers["X-Forwarded-For"]
        peername = (
            request.transport.get_extra_info("peername") if request.transport else None
        )
        if peername is not None:
            return str(peername[0])
        return None

    def brand_from_request(self, request: web.Request) -> str | None:
        """
        If the brand GET parameter is passed, returns that as a string, otherwise returns None.
        """
        return request.query.get("brand")

    def get_branded_template(
        self,
        brand: str | None,
        template_name: str,
    ) -> str:
        """
        Calculate a branded template filename to use.

        Attempt to use the hinted brand from the request if the brand
        is valid. Otherwise, fallback to the default brand.
        """
        if brand:
            if brand not in self.config.general.valid_brands:
                brand = None

        if not brand:
            brand = self.config.general.default_brand

        root_template_path = self.config.general.templates_path

        if os.path.exists(
            os.path.join(root_template_path, brand, template_name + ".j2")
        ):
            return os.path.join(brand, template_name + ".j2")
        else:
            return os.path.join(root_template_path, brand, template_name)

    async def _periodic_cleanup(self, store: ThreePidValSessionStore) -> None:
        """Periodically clean up old validation sessions."""
        while True:
            try:
                await asyncio.sleep(10 * 60.0)  # 10 minutes
                store.deleteOldSessions()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in periodic session cleanup")

    async def _periodic_gc(self) -> None:
        """Periodically run garbage collection (when sentry disables auto-GC)."""
        while True:
            try:
                await asyncio.sleep(1.0)
                run_gc()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in periodic GC")


@attr.s(frozen=True, slots=True, auto_attribs=True)
class Validators:
    email: EmailValidator
    msisdn: MsisdnValidator


@attr.s(frozen=True, slots=True, auto_attribs=True)
class Keyring:
    ed25519: SigningKey


def get_config_file_path() -> str:
    return os.environ.get("SYDENT_CONF", "sydent.conf")


def run_gc() -> None:
    threshold = gc.get_threshold()
    counts = gc.get_count()
    for i in reversed(range(len(threshold))):
        if threshold[i] < counts[i]:
            gc.collect(i)


def setup_logging(config: SydentConfig) -> None:
    """
    Setup logging using the options specified in the config.
    """
    log_path = config.general.log_path
    log_level = config.general.log_level

    log_format = "%(asctime)s - %(name)s - %(lineno)d - %(levelname)s - %(message)s"
    formatter = logging.Formatter(log_format)

    handler: logging.Handler
    if log_path != "":
        handler = logging.handlers.TimedRotatingFileHandler(
            log_path, when="midnight", backupCount=365
        )
        handler.setFormatter(formatter)
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(formatter)
    rootLogger = logging.getLogger("")
    rootLogger.setLevel(log_level)
    rootLogger.addHandler(handler)


def main() -> None:
    sydent_config = SydentConfig()
    sydent_config.parse_config_file(get_config_file_path())
    setup_logging(sydent_config)

    syd = Sydent(sydent_config)
    asyncio.run(syd.run())


if __name__ == "__main__":
    main()
