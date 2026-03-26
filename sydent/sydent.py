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

import gc
import logging
import logging.handlers
import os
import sqlite3
from typing import Optional

import attr
import prometheus_client
import twisted.internet.reactor
from matrix_common.versionstring import get_distribution_version_string
from signedjson.types import SigningKey
from twisted.internet import address, task
from twisted.internet.interfaces import (
    IReactorCore,
    IReactorPluggableNameResolver,
    IReactorSSL,
    IReactorTCP,
    IReactorTime,
)
from twisted.python import log
from twisted.web.http import Request
from zope.interface import Interface

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
from sydent.util.ip_range import generate_ip_set, DEFAULT_IP_RANGE_BLACKLIST
from sydent.http.servlets.infoservlet import InfoServlet
from sydent.http.servlets.internalinfoservlet import InternalInfoServlet
from sydent.http.servlets.profilereplicationservlet import ProfileReplicationServlet
from sydent.http.servlets.userdirectorysearchservlet import UserDirectorySearchServlet
from sydent.http.info import Info

logger = logging.getLogger(__name__)

CONFIG_DEFAULTS = {
    'general': {
        'server.name': os.environ.get('SYDENT_SERVER_NAME', ''),
        'log.path': '',
        'log.level': 'INFO',
        'pidfile.path': os.environ.get('SYDENT_PID_FILE', 'sydent.pid'),
        'terms.path': '',
        'address_lookup_limit': '10000',  # Maximum amount of addresses in a single /lookup request
        'shadow.hs.master': '',
        'shadow.hs.slave': '',
        'ips.nonshadow': '',  # comma separated list of CIDR ranges which /info will return non-shadow HS to.
        # Timestamp in milliseconds, or string in the form of e.g. "2w" for two weeks,
        # which defines the time during which an invite will be valid on this server
        # from the time it has been received.
        'invites.validity_period': '',
        # Path to file detailing the configuration of the /info and /internal-info servlets.
        # More information can be found in docs/info.md.
        'info_path': 'info.yaml',
        # A comma-separated domain whitelist used to validate the next_link query parameter
        # provided by the client to the /requestToken and /submitToken endpoints
        # If empty, no whitelist is applied
        'next_link.domain_whitelist': '',

        # The root path to use for load templates. This should contain branded
        # directories. Each directory should contain the following templates:
        #
        # * invite_template.eml
        # * verification_template.eml
        # * verify_response_template.html
        'templates.path': 'res',
        # The brand directory to use if no brand hint (or an invalid brand hint)
        # is provided by the request.
        'brand.default': 'matrix-org',

        # The following can be added to your local config file to enable prometheus
        # support.
        # 'prometheus_port': '8080',  # The port to serve metrics on
        # 'prometheus_addr': '',  # The address to bind to. Empty string means bind to all.

        # The following can be added to your local config file to enable sentry support.
        # 'sentry_dsn': 'https://...'  # The DSN has configured in the sentry instance project.

        # Whether clients and homeservers can register an association using v1 endpoints.
        'enable_v1_associations': 'true',
        'delete_tokens_on_bind': 'true',

        # Prevent outgoing requests from being sent to the following blacklisted
        # IP address CIDR ranges. If this option is not specified or empty then
        # it defaults to private IP address ranges.
        #
        # The blacklist applies to all outbound requests except replication
        # requests.
        #
        # (0.0.0.0 and :: are always blacklisted, whether or not they are
        # explicitly listed here, since they correspond to unroutable
        # addresses.)
        'ip.blacklist': '',

        # List of IP address CIDR ranges that should be allowed for outbound
        # requests. This is useful for specifying exceptions to wide-ranging
        # blacklisted target IP ranges.
        #
        # This whitelist overrides `ip.blacklist` and defaults to an empty
        # list.
        'ip.whitelist': '',
    },
    'db': {
        'db.file': os.environ.get('SYDENT_DB_PATH', 'sydent.db'),
    },
    'http': {
        'clientapi.http.bind_address': '::',
        'clientapi.http.port': '8090',
        'internalapi.http.bind_address': '::1',
        'internalapi.http.port': '',
        'replication.https.certfile': '',
        'replication.https.cacert': '', # This should only be used for testing
        'replication.https.bind_address': '::',
        'replication.https.port': '4434',
        'obey_x_forwarded_for': 'False',
        'federation.verifycerts': 'True',
        # verify_response_template is deprecated, but still used if defined Define
        # templates.path and brand.default under general instead.
        #
        # 'verify_response_template': 'res/verify_response_page_template',
        'client_http_base': '',
    },
    'email': {
        # email.template and email.invite_template are deprecated, but still used
        # if defined. Define templates.path and brand.default under general instead.
        #
        # 'email.template': 'res/verification_template.eml',
        # 'email.invite_template': 'res/invite_template.eml',
        'email.from': 'Sydent Validation <noreply@{hostname}>',
        'email.subject': 'Your Validation Token',
        'email.invite.subject': '%(sender_display_name)s has invited you to chat',
        'email.smtphost': 'localhost',
        'email.smtpport': '25',
        'email.smtpusername': '',
        'email.smtppassword': '',
        'email.hostname': '',
        'email.tlsmode': '0',
        # The web client location which will be used if it is not provided by
        # the homeserver.
        #
        # This should be the scheme and hostname only, see res/invite_template.eml
        # for the full URL that gets generated.
        'email.default_web_client_location': 'https://app.element.io',

        # When a user is invited to a room via their email address, that invite is
        # displayed in the room list using an obfuscated version of the user's email
        # address. These config options determine how much of the email address to
        # obfuscate. Note that the '@' sign is always included.
        #
        # If the string is longer than a configured limit below, it is truncated to that limit
        # with '...' added. Otherwise:
        #
        # * If the string is longer than 5 characters, it is truncated to 3 characters + '...'
        # * If the string is longer than 1 character, it is truncated to 1 character + '...'
        # * If the string is 1 character long, it is converted to '...'
        #
        # This ensures that a full email address is never shown, even if it is extremely
        # short.
        #
        # The number of characters from the beginning to reveal of the email's username
        # portion (left of the '@' sign)
        'email.third_party_invite_username_reveal_characters': '3',
        # Legacy name equivalent to the above option
        'email.third_party_invite_username_obfuscate_characters': '3',

        # The number of characters from the beginning to reveal of the email's domain
        # portion (right of the '@' sign)
        'email.third_party_invite_domain_reveal_characters': '3',
        # Legacy name equivalent to the above option
        'email.third_party_invite_domain_obfuscate_characters': '3',

        # A string to separate multiple components of the username portion of an email address.
        # For instance, if "-" is set, then "alice-smith@example.com" would result
        # in both "alice" and "smith" being individually obfuscated. Resulting in
        # "ali...-smi...@example.com" for example.
        #
        # The obfuscation amount for each component is set via the
        # `third_party_invite_username_reveal_characters` config option.
        #
        # The default value is an empty string, meaning this option is ignored. In that case,
        # the username is considered a single component.
        'email.third_party_invite_username_separator_string': '',

        # Adds an extra layer of obfuscation, ensuring that even in the case of a username, domain
        # or component containing very few characters - the entire string will not be shown.
        #
        # The algorithm works like so:
        #   * If the string's length is greater than the cutoff value specified
        #     by the above options, stop. Otherwise,
        #   * If the string's length > 5, obfuscate to 3 characters.
        #   * If the string's length > 1, obfuscate to 1 character.
        #
        # The default value is "true".
        'email.always_obfuscate': 'true',
    },
    'sms': {
        'bodyTemplate': 'Your code is {token}',
        'username': '',
        'password': '',
    },
    'crypto': {
        'ed25519.signingkey': '',
    },
    'userdir': {
        'userdir.allowed_homeservers': '',
    },
}


class SydentReactor(
    IReactorCore,
    IReactorTCP,
    IReactorSSL,
    IReactorTime,
    IReactorPluggableNameResolver,
    Interface,
):
    pass


class Sydent:
    def __init__(
        self,
        sydent_config: SydentConfig,
        reactor: SydentReactor = twisted.internet.reactor,  # type: ignore[assignment]
        use_tls_for_federation: bool = True,
    ):
        self.config = sydent_config

        self.reactor = reactor
        self.use_tls_for_federation = use_tls_for_federation

        logger.info("Starting Sydent server")

        self.db: sqlite3.Connection = SqliteDatabase(self).db

        self.cfg = parse_config_file(get_config_file_path())

        self.nonshadow_ips = None
        ips = self.cfg.get('general', "ips.nonshadow")
        if ips:
            self.nonshadow_ips = IPSet()
            for ip in ips.split(','):
                self.nonshadow_ips.add(IPNetwork(ip))

        self.shadow_hs_master = self.cfg.get('general', 'shadow.hs.master')
        self.shadow_hs_slave  = self.cfg.get('general', 'shadow.hs.slave')

        self.user_dir_allowed_hses = set_from_comma_sep_string(
            self.cfg.get('userdir', 'userdir.allowed_homeservers')
        )

        next_link_whitelist = self.cfg.get('general', 'next_link.domain_whitelist')
        if next_link_whitelist == '':
            self.next_link_domain_whitelist = None
        else:
            self.next_link_domain_whitelist = set_from_comma_sep_string(next_link_whitelist)

        self.invites_validity_period = parse_duration(
            self.cfg.get('general', 'invites.validity_period'),
        )

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
            cb = task.LoopingCall(run_gc)
            cb.clock = self.reactor
            cb.start(1.0)

        if self.config.general.prometheus_enabled:
            prometheus_client.start_http_server(
                port=self.config.general.prometheus_port,
                addr=self.config.general.prometheus_addr,
            )

        self.delete_tokens_on_bind = self.config.general.delete_tokens_on_bind

        self.username_reveal_characters = int(self.cfg.get(
            "email", "email.third_party_invite_username_reveal_characters"
        ))

        # Fallback to the old config option name if the new one is not set.
        # There isn't a clear way to check if a config option is set or not, so we have
        # to rely on comparing with default values.
        if (
           self.username_reveal_characters ==
           int(CONFIG_DEFAULTS["email"]["email.third_party_invite_username_reveal_characters"])
        ):
            # This value is no different from the default. Let's take the value of the
            # old option instead (which will also fall back to the default if not set)
            self.username_reveal_characters = int(self.cfg.get(
                "email", "email.third_party_invite_username_obfuscate_characters"
            ))

        self.domain_reveal_characters = int(self.cfg.get(
            "email", "email.third_party_invite_domain_reveal_characters"
        ))

        # Do the same fallback dance for this option
        if (
            self.domain_reveal_characters ==
            int(CONFIG_DEFAULTS["email"]["email.third_party_invite_domain_reveal_characters"])
        ):
            self.domain_reveal_characters = int(self.cfg.get(
                "email", "email.third_party_invite_domain_obfuscate_characters"
            ))

        self.third_party_invite_username_separator_string = self.cfg.get(
            "email", "email.third_party_invite_username_separator_string"
        )

        self.always_obfuscate = parse_cfg_bool(
            self.cfg.get("email", "email.always_obfuscate")
        )

        # See if a pepper already exists in the database
        # Note: This MUST be run before we start serving requests, otherwise lookups for
        # 3PID hashes may come in before we've completed generating them
        hashing_metadata_store = HashingMetadataStore(self)
        lookup_pepper = hashing_metadata_store.get_lookup_pepper()
        if not lookup_pepper:
            # No pepper defined in the database, generate one
            lookup_pepper = generateAlphanumericTokenOfLength(5)

            # Store it in the database and rehash 3PIDs
            hashing_metadata_store.store_lookup_pepper(
                sha256_and_url_safe_base64, lookup_pepper
            )

        self.validators: Validators = Validators(
            EmailValidator(self), MsisdnValidator(self)
        )

        self.keyring: Keyring = Keyring(self.config.crypto.signing_key)
        self.keyring.ed25519.alg = "ed25519"

        self.sig_verifier: Verifier = Verifier(self)

        self.servlets = Servlets()
        self.servlets.v1 = V1Servlet(self)
        self.servlets.v2 = V2Servlet(self)
        self.servlets.emailRequestCode = EmailRequestCodeServlet(self)
        self.servlets.emailRequestCodeV2 = EmailRequestCodeServlet(self, require_auth=True)
        self.servlets.emailValidate = EmailValidateCodeServlet(self)
        self.servlets.emailValidateV2 = EmailValidateCodeServlet(self, require_auth=True)
        self.servlets.msisdnRequestCode = MsisdnRequestCodeServlet(self)
        self.servlets.msisdnRequestCodeV2 = MsisdnRequestCodeServlet(self, require_auth=True)
        self.servlets.msisdnValidate = MsisdnValidateCodeServlet(self)
        self.servlets.msisdnValidateV2 = MsisdnValidateCodeServlet(self, require_auth=True)
        self.servlets.lookup = LookupServlet(self)
        self.servlets.bulk_lookup = BulkLookupServlet(self)
        self.servlets.hash_details = HashDetailsServlet(self, lookup_pepper)
        self.servlets.lookup_v2 = LookupV2Servlet(self, lookup_pepper)
        self.servlets.pubkey_ed25519 = Ed25519Servlet(self)
        self.servlets.pubkeyIsValid = PubkeyIsValidServlet(self)
        self.servlets.ephemeralPubkeyIsValid = EphemeralPubkeyIsValidServlet(self)
        self.servlets.threepidBind = ThreePidBindServlet(self)
        self.servlets.threepidBindV2 = ThreePidBindServlet(self, require_auth=True)
        self.servlets.threepidUnbind = ThreePidUnbindServlet(self)
        self.servlets.replicationPush = ReplicationPushServlet(self)
        self.servlets.getValidated3pid = GetValidated3pidServlet(self)
        self.servlets.getValidated3pidV2 = GetValidated3pidServlet(self, require_auth=True)
        self.servlets.storeInviteServlet = StoreInviteServlet(self)
        self.servlets.storeInviteServletV2 = StoreInviteServlet(self, require_auth=True)
        self.servlets.blindlySignStuffServlet = BlindlySignStuffServlet(self)
        self.servlets.blindlySignStuffServletV2 = BlindlySignStuffServlet(self, require_auth=True)
        self.servlets.profileReplicationServlet = ProfileReplicationServlet(self)
        self.servlets.userDirectorySearchServlet = UserDirectorySearchServlet(self)
        self.servlets.termsServlet = TermsServlet(self)
        self.servlets.accountServlet = AccountServlet(self)
        self.servlets.registerServlet = RegisterServlet(self)
        self.servlets.logoutServlet = LogoutServlet(self)

        info = Info(self, self.cfg.get("general", "info_path"))
        self.servlets.info = InfoServlet(self, info)
        self.servlets.internalInfo = InternalInfoServlet(self, info)

        self.threepidBinder = ThreepidBinder(self, info)

        self.sslComponents: SslComponents = SslComponents(self)

        self.clientApiHttpServer = ClientApiHttpServer(self, lookup_pepper)
        self.replicationHttpsServer = ReplicationHttpsServer(self)
        self.replicationHttpsClient: ReplicationHttpsClient = ReplicationHttpsClient(
            self
        )

        self.pusher: Pusher = Pusher(self)

        self.email_sender_ratelimiter: Ratelimiter[str] = Ratelimiter(
            self.reactor,
            burst=self.config.email.email_sender_ratelimit_burst,
            rate_hz=self.config.email.email_sender_ratelimit_rate_hz,
        )

    def run(self) -> None:
        self.clientApiHttpServer.setup()
        self.replicationHttpsServer.setup()
        self.pusher.setup()
        self.maybe_start_prometheus_server()

        # A dedicated validation session store just to clean up old sessions every N minutes
        self.cleanupValSession = ThreePidValSessionStore(self)
        cb = task.LoopingCall(self.cleanupValSession.deleteOldSessions)
        cb.clock = self.reactor
        cb.start(10 * 60.0)

        if self.config.http.internal_port is not None:
            internalport = self.config.http.internal_port
            interface = self.config.http.internal_bind_address

            self.internalApiHttpServer = InternalApiHttpServer(self)
            self.internalApiHttpServer.setup(interface, internalport)

        if self.config.general.pidfile:
            with open(self.config.general.pidfile, "w") as pidfile:
                pidfile.write(str(os.getpid()) + "\n")

        self.reactor.run()

    def maybe_start_prometheus_server(self) -> None:
        if self.config.general.prometheus_enabled:
            assert self.config.general.prometheus_addr is not None
            assert self.config.general.prometheus_port is not None
            prometheus_client.start_http_server(
                port=self.config.general.prometheus_port,
                addr=self.config.general.prometheus_addr,
            )

    def ip_from_request(self, request: Request) -> Optional[str]:
        if self.config.http.obey_x_forwarded_for and request.requestHeaders.hasHeader(
            "X-Forwarded-For"
        ):
            # Type safety: hasHeaders returning True means that getRawHeaders
            # returns a nonempty list
            return request.requestHeaders.getRawHeaders("X-Forwarded-For")[0]  # type: ignore[index]
        client = request.getClientAddress()
        if isinstance(client, (address.IPv4Address, address.IPv6Address)):
            return client.host
        else:
            return None

    def brand_from_request(self, request: Request) -> Optional[str]:
        """
        If the brand GET parameter is passed, returns that as a string, otherwise returns None.

        :param request: The incoming request.

        :return: The brand to use or None if no hint is found.
        """
        if b"brand" in request.args:
            return request.args[b"brand"][0].decode("utf-8")
        return None

    def get_branded_template(
        self,
        brand: Optional[str],
        template_name: str,
    ) -> str:
        """
        Calculate a branded template filename to use.

        Attempt to use the hinted brand from the request if the brand
        is valid. Otherwise, fallback to the default brand.

        :param brand: The hint of which brand to use.
        :type brand: str or None
        :param template_name: The name of the template file to load.
        :type template_name: str

        :return: The template filename to use.
        :rtype: str
        """

        # If a brand hint is provided, attempt to use it if it is valid.
        if brand:
            if brand not in self.config.general.valid_brands:
                brand = None

        # If the brand hint is not valid, or not provided, fallback to the default brand.
        if not brand:
            brand = self.config.general.default_brand

        root_template_path = self.config.general.templates_path

        # Grab jinja template if it exists
        if os.path.exists(
            os.path.join(root_template_path, brand, template_name + ".j2")
        ):
            return os.path.join(brand, template_name + ".j2")
        else:
            return os.path.join(root_template_path, brand, template_name)


@attr.s(frozen=True, slots=True, auto_attribs=True)
class Validators:
    email: EmailValidator
    msisdn: MsisdnValidator


@attr.s(frozen=True, slots=True, auto_attribs=True)
class Keyring:
    ed25519: SigningKey


def get_config_file_path() -> str:
    return os.environ.get("SYDENT_CONF", "sydent.conf")


def parse_config_file(config_file):
    """Parse the given config from a filepath, populating missing items and
    sections
    Args:
        config_file (str): the file to be parsed
    """
    # if the config file doesn't exist, prepopulate the config object
    # with the defaults, in the right section.
    #
    # otherwise, we have to put the defaults in the DEFAULT section,
    # to ensure that they don't override anyone's settings which are
    # in their config file in the default section (which is likely,
    # because sydent used to be braindead).
    use_defaults = not os.path.exists(config_file)
    cfg = configparser.ConfigParser()
    for sect, entries in CONFIG_DEFAULTS.items():
        cfg.add_section(sect)
        for k, v in entries.items():
            cfg.set(configparser.DEFAULTSECT if use_defaults else sect, k, v)

    cfg.read(config_file)

    return cfg


def parse_duration(value):
    if not len(value):
        return None

    try:
        return int(value)
    except ValueError:
        pass

    second = 1000
    minute = 60 * second
    hour = 60 * minute
    day = 24 * hour
    week = 7 * day
    year = 365 * day
    sizes = {"s": second, "m": minute, "h": hour, "d": day, "w": week, "y": year}
    size = 1
    suffix = value[-1]
    if suffix in sizes:
        value = value[:-1]
        size = sizes[suffix]
    return int(value) * size


def parse_cfg_bool(value):
    return value.lower() == "true"


def set_from_comma_sep_string(rawstr: str) -> Set[str]:
    if rawstr == '':
        return set()
    return {x.strip() for x in rawstr.split(',')}


def run_gc() -> None:
    threshold = gc.get_threshold()
    counts = gc.get_count()
    for i in reversed(range(len(threshold))):
        if threshold[i] < counts[i]:
            gc.collect(i)


def setup_logging(config: SydentConfig) -> None:
    """
    Setup logging using the options specified in the config

    :param config: the configuration to use
    """
    log_path = config.general.log_path
    log_level = config.general.log_level

    log_format = "%(asctime)s - %(name)s - %(lineno)d - %(levelname)s" " - %(message)s"
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

    observer = log.PythonLoggingObserver()
    observer.start()


def main() -> None:
    sydent_config = SydentConfig()
    sydent_config.parse_config_file(get_config_file_path())
    setup_logging(sydent_config)

    syd = Sydent(sydent_config)
    syd.run()


if __name__ == "__main__":
    main()
