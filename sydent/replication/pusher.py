# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import asyncio
import logging
from typing import TYPE_CHECKING

from sydent.db.peers import PeerStore
from sydent.db.threepid_associations import LocalAssociationStore
from sydent.replication.peer import LocalPeer, RemotePeer
from sydent.util import time_msec

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)

# Maximum amount of signed associations to replicate to a peer at a time
ASSOCIATIONS_PUSH_LIMIT = 100


class Pusher:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent
        self.pushing = False
        self.peerStore = PeerStore(self.sydent)
        self.local_assoc_store = LocalAssociationStore(self.sydent)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.ensure_future(self._push_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _push_loop(self) -> None:
        """Periodically push updates to all known remote peers."""
        while True:
            try:
                await self.scheduledPush()
            except Exception:
                logger.exception("Error in scheduled push loop")
            await asyncio.sleep(10.0)

    def doLocalPush(self) -> None:
        """
        Synchronously push local associations to this server (ie. copy them
        to the globals table). The local server is essentially treated the
        same as any other peer except we don't do the network round-trip and
        this function can be used so the association goes into the global
        table before the HTTP call returns.
        """
        localPeer = LocalPeer(self.sydent)

        signedAssocs, _ = self.local_assoc_store.getSignedAssociationsAfterId(
            localPeer.lastId, None
        )

        # LocalPeer.pushUpdates is async (to satisfy the parent Peer interface)
        # but performs no actual I/O, so we schedule it as a task.
        import asyncio

        asyncio.get_event_loop().create_task(localPeer.pushUpdates(signedAssocs))

    async def scheduledPush(self) -> None:
        """Push pending updates to all known remote peers."""
        peers = self.peerStore.getAllPeers()

        tasks = [self._push_to_peer(p) for p in peers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _push_to_peer(self, p: "RemotePeer") -> None:
        """
        For a given peer, retrieves the list of associations that were created
        since the last successful push to this peer (limited to
        ASSOCIATIONS_PUSH_LIMIT) and sends them.

        :param p: The peer to send associations to.
        """
        logger.debug("Looking for updates to push to %s", p.servername)

        # Check if a push operation is already active. If so, don't start another
        if p.is_being_pushed_to:
            logger.debug(
                "Waiting for %s to finish pushing...", p.replication_url_origin
            )
            return

        p.is_being_pushed_to = True

        try:
            # Push associations
            (
                assocs,
                latest_assoc_id,
            ) = self.local_assoc_store.getSignedAssociationsAfterId(
                p.lastSentVersion, ASSOCIATIONS_PUSH_LIMIT
            )

            # If there are no updates left to send, break the loop
            if not assocs:
                return

            logger.info(
                "Pushing %d updates to %s", len(assocs), p.replication_url_origin
            )
            result = await p.pushUpdates(assocs)

            self.peerStore.setLastSentVersionAndPokeSucceeded(
                p.servername, latest_assoc_id, time_msec()
            )

            logger.info(
                "Pushed updates to %s with result %d %s",
                p.replication_url_origin,
                result.status,
                result.reason,
            )
        except Exception:
            logger.exception("Error pushing updates to %s", p.replication_url_origin)
        finally:
            p.is_being_pushed_to = False
