# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from sydent.replication.peer import RemotePeer

if TYPE_CHECKING:
    from sydent.sydent import Sydent


class PeerStore:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent

    def getPeerByName(self, name: str) -> Optional[RemotePeer]:
        """
        Retrieves a remote peer using it's server name.

        :param name: The server name of the peer.

        :return: The retrieved peer.
        """
        cur = self.sydent.db.cursor()
        res = cur.execute(
            "select p.name, p.port, "
            "p.lastSentAssocsId, p.lastSentInviteTokensId, p.lastSentInviteUpdatesId, p.lastSentEphemeralKeysId, "
            "p.shadow, pk.alg, pk.key from peers p, peer_pubkeys pk "
            "where p.name = ? and pk.peername = p.name and p.active = 1",
            (name,),
        )

        serverName: str = None  # type: ignore[assignment]
        port: Optional[int] = None
        lastSentAssocsId: Optional[int] = None
        lastSentInviteTokensId: Optional[int] = None
        lastSentInviteUpdatesId: Optional[int] = None
        lastSentEphemeralKeysId: Optional[int] = None
        pubkeys: Dict[str, str] = {}

        row: Tuple[str, Optional[int], Optional[int], str, str]
        for row in res.fetchall():
            serverName = row[0]
            port = row[1]
            lastSentAssocsId = row[2]
            lastSentInviteTokensId = row[3]
            lastSentInviteUpdatesId = row[4]
            lastSentEphemeralKeysId = row[5]
            shadow = row[6]
            pubkeys[row[7]] = row[8]

        if len(pubkeys) == 0:
            return None

        p = RemotePeer(
            self.sydent, serverName, port, pubkeys, lastSentAssocsId,
            lastSentInviteTokensId, lastSentInviteUpdatesId, lastSentEphemeralKeysId,
            True if shadow else False
        )

        return p

    def getAllPeers(self) -> List[RemotePeer]:
        """
        Retrieve all of the remote peers from the database.

        :return: A list of the remote peers this server knows about.
        """
        cur = self.sydent.db.cursor()
        res = cur.execute(
            "select p.name, p.port, "
            "p.lastSentAssocsId, p.lastSentInviteTokensId, p.lastSentInviteUpdatesId, p.lastSentEphemeralKeysId, "
            "p.shadow, pk.alg, pk.key from peers p, peer_pubkeys pk "
            "where pk.peername = p.name and p.active = 1"
        )

        peers = []

        # Safety: we need to convince ourselves that `peername` will be not None
        # when passed to `RemotePeer`.
        #
        # If `res` is empty, then `pubkeys` will start empty and never be written to.
        # So we will never create a `RemotePeer`. That's fine.
        #
        # Otherwise we process at least one row. The first row we process will
        # satisfy `row[0] is not None` because `name` is nonnull in the schema.
        # `pubkeys` will be empty, so we skip the innermost `if` and assign peername
        # to be a string. There are no further assignments of `None` to `peername`;
        # it will be a string whenever we use it.
        peername: str = None  # type: ignore[assignment]
        port = None
        lastSentAssocsId: int = 0
        lastSentInviteTokensId: int = 0
        lastSentInviteUpdatesId: int = 0
        lastSentEphemeralKeysId: int = 0
        pubkeys: Dict[str, str] = {}

        row: Tuple[str, Optional[int], Optional[int], str, str]
        for row in res.fetchall():
            if row[0] != peername:
                if len(pubkeys) > 0:
                    p = RemotePeer(
                        self.sydent, peername, port, pubkeys, lastSentAssocsId,
                        lastSentInviteTokensId, lastSentInviteUpdatesId,
                        lastSentEphemeralKeysId, True if shadow else False
                    )
                    peers.append(p)
                    pubkeys = {}
                peername = row[0]
                port = row[1]
                lastSentAssocsId = row[2]
                lastSentInviteTokensId = row[3]
                lastSentInviteUpdatesId = row[4]
                lastSentEphemeralKeysId = row[5]
                shadow = row[6]
            pubkeys[row[7]] = row[8]

        if len(pubkeys) > 0:
            p = RemotePeer(
                self.sydent, peername, port, pubkeys, lastSentAssocsId,
                lastSentInviteTokensId, lastSentInviteUpdatesId,
                lastSentEphemeralKeysId, True if shadow else False
            )
            peers.append(p)

        return peers

    def setLastSentIdAndPokeSucceeded(self, peerName: str, ids: Dict, lastPokeSucceeded: Optional[int]) -> None:
        """
        Sets the ID of the last association sent to a given peer and the time of the
        last successful request sent to that peer.

        :param peerName: The server name of the peer.
        :param ids: The ID of the last instance of each type of replicated data sent to
            the peer.
        :param lastPokeSucceeded: The timestamp in milliseconds of the last successful
            request sent to that peer.
        """
        invite_token_ids = ids.get("invite_tokens", {})

        cur = self.sydent.db.cursor()
        if ids["sg_assocs"]:
            cur.execute(
                "update peers set lastSentAssocsId = ?, lastPokeSucceededAt = ? where name = ?",
                (ids["sg_assocs"], lastPokeSucceeded, peerName),
            )
        if invite_token_ids.get("added"):
            cur.execute(
                "update peers set lastSentInviteTokensId = ?, lastPokeSucceededAt = ? where name = ?",
                (invite_token_ids["added"], lastPokeSucceeded, peerName),
            )
        if invite_token_ids.get("updated"):
            cur.execute(
                "update peers set lastSentInviteUpdatesId = ?, lastPokeSucceededAt = ? where name = ?",
                (invite_token_ids["updated"], lastPokeSucceeded, peerName),
            )
        if ids.get("ephemeral_public_keys"):
            cur.execute(
                "update peers set lastSentEphemeralKeysId = ?, lastPokeSucceededAt = ? where name = ?",
                (ids["ephemeral_public_keys"], lastPokeSucceeded, peerName),
            )
        self.sydent.db.commit()
