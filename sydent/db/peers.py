# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from typing import TYPE_CHECKING

from sydent.replication.peer import RemotePeer

if TYPE_CHECKING:
    from sydent.sydent import Sydent


class PeerStore:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent

    def getPeerByName(self, name: str) -> RemotePeer | None:
        """
        Retrieves a remote peer using it's server name.

        :param name: The server name of the peer.

        :return: The retrieved peer.
        """
        cur = self.sydent.db.cursor()
        res = cur.execute(
            "select p.name, p.port, p.lastSentVersion, pk.alg, pk.key from peers p, peer_pubkeys pk "
            "where p.name = ? and pk.peername = p.name and p.active = 1",
            (name,),
        )

        # Type safety: if the query returns no rows, pubkeys will be empty
        # and we'll return None before using serverName. Otherwise, we'll read
        # at least one row and assign serverName a string value, because the
        # `name` column is declared `not null` in the DB.
        serverName: str = None  # type: ignore[assignment]
        port: int | None = None
        lastSentVer: int | None = None
        pubkeys: dict[str, str] = {}

        row: tuple[str, int | None, int | None, str, str]
        for row in res.fetchall():
            serverName = row[0]
            port = row[1]
            lastSentVer = row[2]
            pubkeys[row[3]] = row[4]

        if len(pubkeys) == 0:
            return None

        p = RemotePeer(self.sydent, serverName, port, pubkeys, lastSentVer)

        return p

    def getAllPeers(self) -> list[RemotePeer]:
        """
        Retrieve all of the remote peers from the database.

        :return: A list of the remote peers this server knows about.
        """
        cur = self.sydent.db.cursor()
        res = cur.execute(
            "select p.name, p.port, p.lastSentVersion, pk.alg, pk.key from peers p, peer_pubkeys pk "
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
        lastSentVer = None
        pubkeys: dict[str, str] = {}

        row: tuple[str, int | None, int | None, str, str]
        for row in res.fetchall():
            if row[0] != peername:
                if len(pubkeys) > 0:
                    p = RemotePeer(self.sydent, peername, port, pubkeys, lastSentVer)
                    peers.append(p)
                    pubkeys = {}
                peername = row[0]
                port = row[1]
                lastSentVer = row[2]
            pubkeys[row[3]] = row[4]

        if len(pubkeys) > 0:
            p = RemotePeer(self.sydent, peername, port, pubkeys, lastSentVer)
            peers.append(p)

        return peers

    def setLastSentVersionAndPokeSucceeded(
        self,
        peerName: str,
        lastSentVersion: int | None,
        lastPokeSucceeded: int | None,
    ) -> None:
        """
        Sets the ID of the last association sent to a given peer and the time of the
        last successful request sent to that peer.

        :param peerName: The server name of the peer.
        :param lastSentVersion: The ID of the last association sent to that peer.
        :param lastPokeSucceeded: The timestamp in milliseconds of the last successful
            request sent to that peer.
        """
        cur = self.sydent.db.cursor()
        cur.execute(
            "update peers set lastSentVersion = ?, lastPokeSucceededAt = ? "
            "where name = ?",
            (lastSentVersion, lastPokeSucceeded, peerName),
        )
        self.sydent.db.commit()
