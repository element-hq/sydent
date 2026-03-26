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

import logging
from typing import TYPE_CHECKING

import twisted.internet.ssl
from twisted.web.resource import Resource
from twisted.web.server import Site

from sydent.http.httpcommon import SizeLimitingRequest
from sydent.http.servlets.accountservlet import AccountServlet
from sydent.http.servlets.authenticated_bind_threepid_servlet import (
    AuthenticatedBindThreePidServlet,
)
from sydent.http.servlets.authenticated_unbind_threepid_servlet import (
    AuthenticatedUnbindThreePidServlet,
)
from sydent.http.servlets.blindlysignstuffservlet import BlindlySignStuffServlet
from sydent.http.servlets.bulklookupservlet import BulkLookupServlet
from sydent.http.servlets.cors_servlet import CorsServlet
from sydent.http.servlets.emailservlet import (
    EmailRequestCodeServlet,
    EmailValidateCodeServlet,
)
from sydent.http.servlets.getvalidated3pidservlet import GetValidated3pidServlet
from sydent.http.servlets.hashdetailsservlet import HashDetailsServlet
from sydent.http.servlets.logoutservlet import LogoutServlet
from sydent.http.servlets.lookupservlet import LookupServlet
from sydent.http.servlets.lookupv2servlet import LookupV2Servlet
from sydent.http.servlets.msisdnservlet import (
    MsisdnRequestCodeServlet,
    MsisdnValidateCodeServlet,
)
from sydent.http.servlets.pubkeyservlets import (
    Ed25519Servlet,
    EphemeralPubkeyIsValidServlet,
    PubkeyIsValidServlet,
)
from sydent.http.servlets.registerservlet import RegisterServlet
from sydent.http.servlets.replication import ReplicationPushServlet
from sydent.http.servlets.store_invite_servlet import StoreInviteServlet
from sydent.http.servlets.termsservlet import TermsServlet
from sydent.http.servlets.threepidbindservlet import ThreePidBindServlet
from sydent.http.servlets.threepidunbindservlet import ThreePidUnbindServlet
from sydent.http.servlets.versions import VersionsServlet

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)


class ClientApiHttpServer:
    def __init__(self, sydent: "Sydent", lookup_pepper: str) -> None:
        """
        Args:
            lookup_pepper: The pepper used when hashing identifiers.

        """
        self.sydent = sydent

        root = Resource()
        matrix = Resource()
        identity = Resource()
        api = Resource()
        v1 = CorsServlet(sydent)
        v2 = CorsServlet(sydent)

        validate = Resource()
        validate_v2 = Resource()
        email = Resource()
        email_v2 = Resource()
        msisdn = Resource()
        msisdn_v2 = Resource()

        info = self.sydent.servlets.info
        internalInfo = self.sydent.servlets.internalInfo

        threepid_v1 = Resource()
        threepid_v2 = Resource()
        unbind = ThreePidUnbindServlet(sydent)

        pubkey = Resource()
        ephemeralPubkey = Resource()

        userDirectory = Resource()

        root.putChild(b"_matrix", matrix)
        matrix.putChild(b"identity", identity)
        identity.putChild(b"api", api)
        identity.putChild(b"v2", v2)
        identity.putChild(b"versions", VersionsServlet())
        api.putChild(b"v1", v1)

        pubkey.putChild(b"isvalid", PubkeyIsValidServlet(sydent))
        pubkey.putChild(b"ed25519:0", Ed25519Servlet(sydent))
        pubkey.putChild(b"ephemeral", ephemeralPubkey)
        ephemeralPubkey.putChild(b"isvalid", EphemeralPubkeyIsValidServlet(sydent))

        # v1
        if self.sydent.config.general.enable_v1_access:
            api.putChild(b"v1", v1)
            validate.putChild(b"email", email)
            validate.putChild(b"msisdn", msisdn)
            v1.putChild(b"validate", validate)

            v1.putChild(b"lookup", LookupServlet(sydent))
            v1.putChild(b"bulk_lookup", BulkLookupServlet(sydent))

            v1.putChild(b"pubkey", pubkey)

            threepid_v1.putChild(b"getValidated3pid", GetValidated3pidServlet(sydent))
            threepid_v1.putChild(b"unbind", unbind)
            v1.putChild(b"3pid", threepid_v1)

            email.putChild(b"requestToken", EmailRequestCodeServlet(sydent))
            email.putChild(b"submitToken", EmailValidateCodeServlet(sydent))

            msisdn.putChild(b"requestToken", MsisdnRequestCodeServlet(sydent))
            msisdn.putChild(b"submitToken", MsisdnValidateCodeServlet(sydent))

            v1.putChild(b"store-invite", StoreInviteServlet(sydent))
            v1.putChild(b"sign-ed25519", BlindlySignStuffServlet(sydent))

        v1.putChild(b"info", info)
        v1.putChild(b"internal-info", internalInfo)

        if self.sydent.config.general.enable_v1_associations:
            threepid_v1.putChild(b"bind", ThreePidBindServlet(sydent))

        v1.putChild(b'user_directory', userDirectory)

        userDirectory.putChild(b'search', self.sydent.servlets.userDirectorySearchServlet)

        v1.putChild(b'replicate_profiles', self.sydent.servlets.profileReplicationServlet)

        # v2
        # note v2 loses the /api so goes on 'identity' not 'api'
        identity.putChild(b"v2", v2)

        validate_v2.putChild(b"email", email_v2)
        validate_v2.putChild(b"msisdn", msisdn_v2)

        threepid_v2.putChild(
            b"getValidated3pid", GetValidated3pidServlet(sydent, require_auth=True)
        )
        threepid_v2.putChild(b"bind", ThreePidBindServlet(sydent, require_auth=True))
        threepid_v2.putChild(b"unbind", unbind)

        email_v2.putChild(
            b"requestToken", EmailRequestCodeServlet(sydent, require_auth=True)
        )
        email_v2.putChild(
            b"submitToken", EmailValidateCodeServlet(sydent, require_auth=True)
        )

        msisdn_v2.putChild(
            b"requestToken", MsisdnRequestCodeServlet(sydent, require_auth=True)
        )
        msisdn_v2.putChild(
            b"submitToken", MsisdnValidateCodeServlet(sydent, require_auth=True)
        )

        # v2 exclusive APIs
        v2.putChild(b"terms", TermsServlet(sydent))
        account = AccountServlet(sydent)
        v2.putChild(b"account", account)
        account.putChild(b"register", RegisterServlet(sydent))
        account.putChild(b"logout", LogoutServlet(sydent))

        # v2 versions of existing APIs
        v2.putChild(b"validate", validate_v2)
        v2.putChild(b"pubkey", pubkey)
        v2.putChild(b"3pid", threepid_v2)
        v2.putChild(b"store-invite", StoreInviteServlet(sydent, require_auth=True))
        v2.putChild(b"sign-ed25519", BlindlySignStuffServlet(sydent, require_auth=True))
        v2.putChild(b"lookup", LookupV2Servlet(sydent, lookup_pepper))
        v2.putChild(b"hash_details", HashDetailsServlet(sydent, lookup_pepper))

        self.factory = Site(root, SizeLimitingRequest)
        self.factory.displayTracebacks = False

    def setup(self) -> None:
        httpPort = self.sydent.config.http.client_port
        interface = self.sydent.config.http.client_bind_address

        logger.info("Starting Client API HTTP server on %s:%d", interface, httpPort)
        self.sydent.reactor.listenTCP(
            httpPort,
            self.factory,
            backlog=50,  # taken from PosixReactorBase.listenTCP
            interface=interface,
        )


class InternalApiHttpServer:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent

    def setup(self, interface: str, port: int) -> None:
        logger.info("Starting Internal API HTTP server on %s:%d", interface, port)
        root = Resource()

        matrix = Resource()
        root.putChild(b"_matrix", matrix)

        identity = Resource()
        matrix.putChild(b"identity", identity)

        internal = Resource()
        identity.putChild(b"internal", internal)

        authenticated_bind = AuthenticatedBindThreePidServlet(self.sydent)
        internal.putChild(b"bind", authenticated_bind)

        authenticated_unbind = AuthenticatedUnbindThreePidServlet(self.sydent)
        internal.putChild(b"unbind", authenticated_unbind)

        factory = Site(root)
        factory.displayTracebacks = False
        self.sydent.reactor.listenTCP(
            port,
            factory,
            backlog=50,  # taken from PosixReactorBase.listenTCP
            interface=interface,
        )


class ReplicationHttpsServer:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent

        root = Resource()
        matrix = Resource()
        identity = Resource()

        root.putChild(b"_matrix", matrix)
        matrix.putChild(b"identity", identity)

        replicate = Resource()
        replV1 = Resource()

        identity.putChild(b"replicate", replicate)
        replicate.putChild(b"v1", replV1)
        replV1.putChild(b"push", ReplicationPushServlet(sydent))

        self.factory = Site(root)
        self.factory.displayTracebacks = False

    def setup(self) -> None:
        httpPort = self.sydent.config.http.replication_port
        interface = self.sydent.config.http.replication_bind_address

        if self.sydent.sslComponents.myPrivateCertificate:
            # We will already have logged a warn if this is absent, so don't do it again
            cert = self.sydent.sslComponents.myPrivateCertificate
            certOptions = twisted.internet.ssl.CertificateOptions(
                privateKey=cert.privateKey.original,
                certificate=cert.original,
                trustRoot=self.sydent.sslComponents.trustRoot,
            )

            logger.info("Loaded server private key and certificate!")
            logger.info(
                "Starting Replication HTTPS server on %s:%d", interface, httpPort
            )

            self.sydent.reactor.listenSSL(
                httpPort,
                self.factory,
                certOptions,
                backlog=50,  # taken from PosixReactorBase.listenTCP
                interface=interface,
            )
