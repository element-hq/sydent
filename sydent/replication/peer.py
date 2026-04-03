# Copyright 2019-2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import binascii
import json
import logging
from abc import abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Generic, TypeVar

import aiohttp
import signedjson.key
import signedjson.sign
from unpaddedbase64 import decode_base64

from sydent.config.exceptions import ConfigError
from sydent.db.hashing_metadata import HashingMetadataStore
from sydent.db.threepid_associations import GlobalAssociationStore, SignedAssociations
from sydent.threepid import threePidAssocFromDict
from sydent.types import JsonDict
from sydent.util import json_decoder
from sydent.util.hash import sha256_and_url_safe_base64
from sydent.util.stringutils import normalise_address

PushUpdateReturn = TypeVar("PushUpdateReturn")

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)

SIGNING_KEY_ALGORITHM = "ed25519"


class Peer(Generic[PushUpdateReturn]):
    def __init__(self, servername: str, pubkeys: dict[str, str]):
        """
        :param servername: The peer's server name.
        :param pubkeys: The peer's public keys in a Dict[key_id, key_b64]
        """
        self.servername = servername
        self.pubkeys = pubkeys
        self.is_being_pushed_to = False

    @abstractmethod
    async def pushUpdates(self, sgAssocs: SignedAssociations) -> PushUpdateReturn:
        """
        :param sgAssocs: Map from originId to sgAssoc, where originId is the id
                         on the creating server and sgAssoc is the json object
                         of the signed association
        """
        ...


class LocalPeer(Peer[bool]):
    """
    The local peer (ourselves: essentially copying from the local associations
    table to the global one).
    """

    def __init__(self, sydent: "Sydent") -> None:
        super().__init__(sydent.config.general.server_name, {})
        self.sydent = sydent
        self.hashing_store = HashingMetadataStore(sydent)

        globalAssocStore = GlobalAssociationStore(self.sydent)
        lastId = globalAssocStore.lastIdFromServer(self.servername)
        self.lastId = lastId if lastId is not None else -1

    def pushUpdates(self, sgAssocs: SignedAssociations) -> bool:
        """
        Saves the given associations in the global associations store. Only
        stores an association if its ID is greater than the last seen ID.

        :param sgAssocs: The associations to save.

        :return: True on success.
        """
        globalAssocStore = GlobalAssociationStore(self.sydent)
        for localId in sgAssocs:
            if localId > self.lastId:
                assocObj = threePidAssocFromDict(sgAssocs[localId])

                # ensure we are casefolding email addresses
                assocObj.address = normalise_address(assocObj.address, assocObj.medium)

                if assocObj.mxid is not None:
                    # Assign a lookup_hash to this association
                    pepper = self.hashing_store.get_lookup_pepper()
                    if not pepper:
                        raise RuntimeError("No lookup_pepper in the database.")
                    str_to_hash = " ".join(
                        [
                            assocObj.address,
                            assocObj.medium,
                            pepper,
                        ],
                    )
                    assocObj.lookup_hash = sha256_and_url_safe_base64(str_to_hash)

                    globalAssocStore.addAssociation(
                        assocObj,
                        json.dumps(sgAssocs[localId]),
                        self.sydent.config.general.server_name,
                        localId,
                    )
                else:
                    globalAssocStore.removeAssociation(
                        assocObj.medium, assocObj.address
                    )

        return True


class RemotePeer(Peer[aiohttp.ClientResponse]):
    def __init__(
        self,
        sydent: "Sydent",
        server_name: str,
        port: int | None,
        pubkeys: dict[str, str],
        lastSentVersion: int | None,
    ) -> None:
        """
        :param sydent: The current Sydent instance.
        :param server_name: The peer's server name.
        :param port: The peer's port. Only used if no replication url is configured.
        :param pubkeys: The peer's public keys in a dict[key_id, key_b64]
        :param lastSentVersion: The ID of the last association sent to the peer.
        """
        super().__init__(server_name, pubkeys)
        self.sydent = sydent
        self.lastSentVersion = lastSentVersion

        # look up or build the replication URL
        replication_url = self.sydent.config.http.base_replication_urls.get(server_name)

        if replication_url is None:
            if not port:
                port = 1001
            replication_url = "https://%s:%i" % (server_name, port)

        if replication_url[-1:] != "/":
            replication_url += "/"

        # Capture the interesting bit of the url for logging.
        self.replication_url_origin = replication_url
        replication_url += "_matrix/identity/replicate/v1/push"
        self.replication_url = replication_url

        # Get verify key for this peer

        # Check if their key is base64 or hex encoded
        pubkey = self.pubkeys[SIGNING_KEY_ALGORITHM]
        try:
            # Check for hex encoding
            int(pubkey, 16)

            # Decode hex into bytes
            pubkey_decoded = binascii.unhexlify(pubkey)

            logger.warning(
                "Peer public key of %s is hex encoded. Please update to base64 encoding",
                server_name,
            )
        except ValueError:
            # Check for base64 encoding
            try:
                pubkey_decoded = decode_base64(pubkey)
            except Exception as e:
                raise ConfigError(
                    f"Unable to decode public key for peer {server_name}: {e}",
                )

        self.verify_key = signedjson.key.decode_verify_key_bytes(
            SIGNING_KEY_ALGORITHM + ":", pubkey_decoded
        )

        # Attach metadata
        self.verify_key.alg = SIGNING_KEY_ALGORITHM
        self.verify_key.version = 0

    def verifySignedAssociation(self, assoc: JsonDict) -> None:
        """Verifies a signature on a signed association. Raises an exception if the
        signature is incorrect or couldn't be verified.

        :param assoc: A signed association.
        """
        if "signatures" not in assoc:
            raise NoSignaturesException()

        key_ids = signedjson.sign.signature_ids(assoc, self.servername)
        if (
            not key_ids
            or len(key_ids) == 0
            or not key_ids[0].startswith(SIGNING_KEY_ALGORITHM + ":")
        ):
            e = NoMatchingSignatureException(
                foundSigs=assoc["signatures"].keys(),
                requiredServername=self.servername,
            )
            raise e

        # Verify the JSON
        signedjson.sign.verify_signed_json(assoc, self.servername, self.verify_key)

    async def pushUpdates(self, sgAssocs: SignedAssociations) -> aiohttp.ClientResponse:
        """
        Pushes the given associations to the peer.

        :param sgAssocs: The associations to push.

        :return: The response to the push request.
        """
        body = {"sgAssocs": sgAssocs}

        response = await self.sydent.replicationHttpsClient.postJson(
            self.replication_url, body
        )
        if response is None:
            raise RuntimeError(f"Unable to push sgAssocs to {self.replication_url}")

        if response.status >= 200 and response.status < 300:
            return response

        # Non-success status: read the body for error details
        resp_body = await response.read()
        try:
            errObj = json_decoder.decode(resp_body.decode("utf8"))
            raise RemotePeerError(errObj)
        except (ValueError, UnicodeDecodeError):
            raise Exception(
                "Push to %s failed with status %d"
                % (self.replication_url, response.status)
            )


class NoSignaturesException(Exception):
    pass


class NoMatchingSignatureException(Exception):
    def __init__(self, foundSigs: Sequence[str], requiredServername: str):
        self.foundSigs = foundSigs
        self.requiredServername = requiredServername

    def __str__(self) -> str:
        return f"Found signatures: {self.foundSigs}, required server name: {self.requiredServername}"


class RemotePeerError(Exception):
    def __init__(self, errorDict: JsonDict):
        self.errorDict = errorDict

    def __str__(self) -> str:
        return repr(self.errorDict)
