# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
# Copyright 2019 New Vector Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import json
import logging
from typing import TYPE_CHECKING, List, cast

import twisted.python.log
from OpenSSL.crypto import X509
from twisted.internet import defer
from twisted.internet.interfaces import ISSLTransport
from twisted.web import server
from twisted.web.resource import Resource
from twisted.web.server import Request

from sydent.db.hashing_metadata import HashingMetadataStore
from sydent.db.invite_tokens import JoinTokenStore
from sydent.db.peers import PeerStore
from sydent.db.threepid_associations import GlobalAssociationStore, SignedAssociations
from sydent.http.servlets import MatrixRestError, SydentResource, deferjsonwrap, jsonwrap
from sydent.replication.peer import (
    NoMatchingSignatureException,
    NoSignaturesException,
    RemotePeerError,
)
from sydent.threepid import threePidAssocFromDict
from sydent.types import JsonDict
from sydent.util import json_decoder
from sydent.util.hash import sha256_and_url_safe_base64
from sydent.util.stringutils import normalise_address
from signedjson.sign import SignatureVerifyException

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)


class ReplicationPushServlet(SydentResource):
    def __init__(self, sydent: "Sydent") -> None:
        super().__init__()
        self.sydent = sydent
        self.hashing_store = HashingMetadataStore(sydent)

    def render_POST(self, request):
        self._async_render_POST(request)
        return server.NOT_DONE_YET

    @deferjsonwrap
    @defer.inlineCallbacks
    def _async_render_POST(self, request):
        """Verify and store replicated information from trusted peer identity servers.

        To prevent data sent from erroneous servers from being stored, we
        initially verify that the sender's certificate contains a commonName
        that we trust. This is checked against the peers stored in the local
        DB. Data is then ingested.

        Replicated associations must each be individually signed by the
        signing key of the remote peer, which we verify using the verifykey
        stored in the local DB.

        Other data does not need to be signed.
        """
        peerCert = request.transport.getPeerCertificate()
        peerCertCn = peerCert.get_subject().commonName

        peerStore = PeerStore(self.sydent)

        peer = peerStore.getPeerByName(peerCertCn)

        if not peer:
            logger.warning(
                "Got connection from %s but no peer found by that name", peerCertCn
            )
            raise MatrixRestError(
                403, "M_UNKNOWN_PEER", "This peer is not known to this server"
            )

        logger.info("Push connection made from peer %s", peer.servername)

        if (
            not request.requestHeaders.hasHeader("Content-Type")
            # Type safety: the hasHeader call returned True, so getRawHeaders()
            # returns a nonempty list.
            or request.requestHeaders.getRawHeaders("Content-Type")[0]  # type: ignore[index]
            != "application/json"
        ):
            logger.warning(
                "Peer %s made push connection with non-JSON content (type: %s)",
                peer.servername,
                # Type safety: the hasHeader call returned True, so getRawHeaders()
                # returns a nonempty list.
                request.requestHeaders.getRawHeaders("Content-Type")[0],  # type: ignore[index]
            )
            raise MatrixRestError(400, "M_NOT_JSON", "This endpoint expects JSON")

        try:
            # json.loads doesn't allow bytes in Python 3.5
            inJson = json_decoder.decode(request.content.read().decode("UTF-8"))
        except ValueError:
            logger.warning(
                "Peer %s made push connection with malformed JSON", peer.servername
            )
            raise MatrixRestError(400, "M_BAD_JSON", "Malformed JSON")

        # Ensure there are data types we can process
        if 'sg_assocs' not in inJson and 'invite_tokens' not in inJson and 'ephemeral_public_keys' not in inJson:
            logger.warning(
                "Peer %s made push connection with no 'sg_assocs', 'invite_tokens' or 'ephemeral_public_keys' keys in JSON",
                peer.servername,
            )
            raise MatrixRestError(400, 'M_BAD_JSON', 'No "sg_assocs", "invite_tokens" or "ephemeral_public_keys" key in JSON')

        # Process signed associations
        sg_assocs = inJson.get('sg_assocs', {})
        sg_assocs = sorted(
            sg_assocs.items(), key=lambda k: int(k[0])
        )

        globalAssocsStore = GlobalAssociationStore(self.sydent)

        # Check that this message is signed by one of our trusted associated peers
        for originId, sgAssoc in sg_assocs:
            try:
                yield peer.verifySignedAssociation(sgAssoc)
                logger.debug(
                    "Signed association from %s with origin ID %s verified",
                    peer.servername,
                    originId,
                )
            except (NoSignaturesException, NoMatchingSignatureException, RemotePeerError, SignatureVerifyException):
                self.sydent.db.rollback()
                logger.warning("Failed to verify signed association from %s with origin ID %s", peer.servername, originId)
                raise MatrixRestError(400, 'M_VERIFICATION_FAILED', 'Signature verification failed')
            except Exception:
                self.sydent.db.rollback()
                logger.error("Failed to verify signed association from %s with origin ID %s", peer.servername, originId)
                raise MatrixRestError(500, 'M_INTERNAL_SERVER_ERROR', 'Signature verification failed')

            assocObj = threePidAssocFromDict(sgAssoc)

            if assocObj.mxid is not None:
                # Calculate the lookup hash with our own pepper for this association
                str_to_hash = ' '.join(
                    [assocObj.address, assocObj.medium,
                     self.hashing_store.get_lookup_pepper()],
                )
                assocObj.lookup_hash = sha256_and_url_safe_base64(str_to_hash)

                # Add the association components and the original signed
                # object (as assocs must be signed when requested by clients)
                globalAssocsStore.addAssociation(assocObj, json.dumps(sgAssoc), peer.servername, originId, commit=False)
            else:
                logger.info("Incoming deletion: removing associations for %s / %s", assocObj.medium, assocObj.address)
                globalAssocsStore.removeAssociation(assocObj.medium, assocObj.address)

            logger.info("Stored association with origin ID %s from %s", originId, peer.servername)

            # if this is an association that matches one of our invite_tokens then we should call the onBind callback
            # at this point, in order to tell the inviting HS that someone out there has just bound the 3PID.
            self.sydent.threepidBinder.notifyPendingInvites(assocObj)

        tokensStore = JoinTokenStore(self.sydent)

        # Process any new invite tokens
        invite_tokens = inJson.get('invite_tokens', {})
        new_invites = invite_tokens.get('added', {})
        new_invites = sorted(
            new_invites.items(), key=lambda k: int(k[0])
        )

        for originId, inviteToken in new_invites:
            tokensStore.storeToken(
                inviteToken['medium'], inviteToken['address'], inviteToken['room_id'],
                inviteToken['sender'], inviteToken['token'],
                originServer=peer.servername, originId=originId, commit=False,
            )
            logger.info("Stored invite token with origin ID %s from %s", originId, peer.servername)

        # Process any invite token updates
        invite_updates = invite_tokens.get('updated', [])
        invite_updates = sorted(
            invite_updates, key=lambda k: int(k["origin_id"])
        )

        for updated_invite in invite_updates:
            tokensStore.updateToken(
                updated_invite['medium'], updated_invite['address'], updated_invite['room_id'],
                updated_invite['sender'], updated_invite['token'], updated_invite['sent_ts'],
                origin_server=updated_invite['origin_server'], origin_id=updated_invite['origin_id'],
                is_deletion=updated_invite.get('is_deletion', False), commit=False,
            )
            logger.info("Stored invite update with origin ID %s from %s", updated_invite['origin_id'], peer.servername)

        # Process any ephemeral public keys
        ephemeral_public_keys = inJson.get("ephemeral_public_keys", {})
        ephemeral_public_keys = sorted(
            ephemeral_public_keys.items(), key=lambda k: int(k[0])
        )

        for originId, ephemeralKey in ephemeral_public_keys:
            tokensStore.storeEphemeralPublicKey(
                ephemeralKey['public_key'],
                persistenceTs=ephemeralKey['persistence_ts'],
                originServer=peer.servername,
                originId=originId,
                commit=False,
            )
            logger.info("Stored ephemeral key with origin ID %s from %s", originId, peer.servername)

        self.sydent.db.commit()
        defer.returnValue({'success': True})
