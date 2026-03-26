# Copyright 2025 New Vector Ltd.
# Copyright 2015 OpenMarket Ltd
# Copyright 2019 New Vector Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.
import logging
import time
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)


class JoinTokenStore:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent

    def storeToken(self, medium, address, roomId, sender, token, originServer=None, originId=None, commit=True):
        """
        Store a new invite token and its metadata. Please note that email
        addresses need to be casefolded before calling this function.

        :param medium: The medium of the 3PID the token is associated to.
        :param normalised_address: The address of the 3PID the token is associated to.
        :param roomId: The ID of the room the 3PID is invited in.
        :param sender: The MXID of the user that sent the invite.
        :param originServer: The server this invite originated from (if
            coming from replication).
        :param originId: The id of the token in the DB of originServer. Used
            for determining if we've already received a token or not.
        :param commit: Whether DB changes should be committed by this
            function (or an external one).
        """
        if originId and originServer:
            # Check if we've already seen this association from this server
            last_processed_id = self.getLastTokenIdFromServer(originServer)
            if int(originId) <= int(last_processed_id):
                logger.info("We have already seen token ID %s from %s. Ignoring.", originId, originServer)
                return

        cur = self.sydent.db.cursor()

        cur.execute(
            "INSERT INTO invite_tokens"
            " ('medium', 'address', 'room_id', 'sender', 'token', 'received_ts', 'origin_server', 'origin_id')"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (medium, address, roomId, sender, token, int(time.time()), originServer, originId),
        )
        if commit:
            self.sydent.db.commit()

    def updateToken(self, medium, address, room_id, sender, token, sent_ts, origin_server, origin_id, is_deletion, commit=True):
        """Process an invite token update or deletion received over replication.

        :param medium: The medium of the token.
        :param address: The address of the token.
        :param room_id: The room ID this token is tied to.
        :param sender: The sender of the invite.
        :param token: The token itself.
        :param sent_ts: The timestamp at which the token has been delivered to the
            invitee (if applicable).
        :param origin_server: The server the original version of the token originated from.
        :param origin_id: The id of the token in the DB of origin_server.
        :param is_deletion: Whether the update is a deletion.
        :param commit: Whether DB changes should be committed by this function.
        """
        cur = self.sydent.db.cursor()

        if is_deletion:
            sql = """
                DELETE FROM invite_tokens
            """
            params = ()
        else:
            sql = """
                UPDATE invite_tokens
                SET
                    medium = ?, address = ?, room_id = ?, sender = ?, token = ?,
                    sent_ts = ?
            """
            params = (medium, address, room_id, sender, token, sent_ts)

        if origin_server == self.sydent.server_name:
            where_clause = """
                WHERE id = ?
            """
            params += (origin_id,)
        else:
            where_clause = """
                WHERE origin_id = ? AND origin_server = ?
            """
            params += (origin_id, origin_server)

        sql += where_clause
        cur.execute(sql, params)

        if commit:
            self.sydent.db.commit()

    def getTokens(self, medium: str, address: str) -> List[Dict[str, str]]:
        """
        Retrieves the pending invites tokens for this 3PID that haven't been delivered
        yet.

        :param medium: The medium of the 3PID to get tokens for.
        :param address: The address of the 3PID to get tokens for.

        :return: A list of dicts, each containing a pending token and its metadata for
            this 3PID.
        """
        cur = self.sydent.db.cursor()

        res = cur.execute(
            "SELECT medium, address, room_id, sender, token, origin_server, received_ts FROM invite_tokens"
            " WHERE medium = ? AND address = ? AND sent_ts IS NULL",
            (
                medium,
                address,
            ),
        )
        rows: List[Tuple[str, str, str, str, str]] = res.fetchall()

        ret = []

        validity_period = self.sydent.invites_validity_period
        if validity_period is not None:
            min_valid_ts_ms = int(time.time() - validity_period/1000)

        for row in rows:
            medium, address, roomId, sender, token, origin_server, received_ts = row

            if (
                validity_period is not None
                and received_ts and received_ts < min_valid_ts_ms
            ):
                # Ignore this invite if it has expired.
                continue

            ret.append({
                "medium": medium,
                "address": address,
                "room_id": roomId,
                "sender": sender,
                "token": token,
                "origin_server": origin_server,
            })

        return ret

    def getInviteTokensAfterId(self, afterId, limit):
        """Retrieves max `limit` invite tokens after a given DB id.

        :param afterId: A database id to act as an offset. Tokens after this id are returned.
        :param limit: Max amount of database rows to return.
        :returns a tuple of (dict of invite tokens keyed by DB id, max DB id), or ({}, None).
        """
        cur = self.sydent.db.cursor()
        res = cur.execute(
            "SELECT id, medium, address, room_id, sender, token FROM invite_tokens"
            " WHERE id > ? AND origin_id IS NULL LIMIT ?",
            (afterId, limit,),
        )
        rows = res.fetchall()

        invite_tokens = {}
        maxId = None

        for row in rows:
            maxId, medium, address, room_id, sender, token = row
            invite_tokens[maxId] = {
                "origin_id": maxId,
                "medium": medium,
                "address": address,
                "room_id": room_id,
                "sender": sender,
                "token": token,
            }

        return invite_tokens, maxId

    def getLastTokenIdFromServer(self, server):
        """Returns the last known invite token that was received from the given server.

        :param server: The name of the origin server.
        :returns a database id marking the last known invite token received from the
            given server. Returns 0 if no tokens have been received from this server.
        """
        cur = self.sydent.db.cursor()
        res = cur.execute(
            "SELECT max(origin_id), count(origin_id) FROM invite_tokens WHERE origin_server = ?",
            (server,),
        )
        row = res.fetchone()

        if row[1] == 0:
            return 0

        return row[0]

    def markTokensAsSent(self, medium: str, address: str) -> None:
        """
        Updates the invite tokens associated with a given 3PID to mark them as
        delivered to a homeserver so they're not delivered again in the future.

        :param medium: The medium of the 3PID to update tokens for.
        :param address: The address of the 3PID to update tokens for.
        """
        cur = self.sydent.db.cursor()

        cur.execute(
            "UPDATE invite_tokens SET sent_ts = ? WHERE medium = ? AND address = ?",
            (
                int(time.time()),
                medium,
                address,
            ),
        )

        # Insert a row for every updated invite in the updated_invites table so the
        # update is replicated to other servers.
        res = cur.execute(
            """
            SELECT id, origin_server, origin_id
            FROM invite_tokens WHERE medium = ? AND address = ?
            """,
            (medium, address,),
        )

        rows = res.fetchall()

        cur.executemany(
            """
            INSERT INTO updated_invites (invite_id, origin_server, origin_id)
            VALUES (?, ?, ?)
            """,
            rows,
        )

        self.sydent.db.commit()

    def storeEphemeralPublicKey(self, publicKey, persistenceTs=None, originServer=None, originId=None, commit=True):
        """
        Saves the provided ephemeral public key.

        :param publicKey: The key to store.
        :param persistenceTs: The time of the key's creation (if received through replication).
        :param originServer: The server this key was received from (if retrieved through replication).
        :param originId: The id of the key in the DB of originServer.
        :param commit: Whether DB changes should be committed by this function.
        """
        if originId and originServer:
            # Check if we've already seen this association from this server
            last_processed_id = self.getLastEphemeralPublicKeyIdFromServer(originServer)
            if int(originId) <= int(last_processed_id):
                logger.info("We have already seen key ID %s from %s. Ignoring.", originId, originServer)
                return

        if not persistenceTs:
            persistenceTs = int(time.time())

        cur = self.sydent.db.cursor()
        cur.execute(
            "INSERT INTO ephemeral_public_keys"
            " (public_key, persistence_ts, origin_server, origin_id)"
            " VALUES (?, ?, ?, ?)",
            (publicKey, persistenceTs, originServer, originId),
        )
        if commit:
            self.sydent.db.commit()

    def validateEphemeralPublicKey(self, publicKey: str) -> bool:
        """
        Checks if an ephemeral public key is valid, and, if it is, updates its
        verification count.

        :param publicKey: The public key to validate.

        :return: Whether the key is valid.
        """
        cur = self.sydent.db.cursor()
        cur.execute(
            "UPDATE ephemeral_public_keys"
            " SET verify_count = verify_count + 1"
            " WHERE public_key = ?",
            (publicKey,),
        )
        self.sydent.db.commit()
        return cur.rowcount > 0

    def getEphemeralPublicKeysAfterId(self, afterId, limit):
        """Retrieves max `limit` ephemeral public keys after a given DB id.

        :param afterId: A database id to act as an offset. Keys after this id are returned.
        :param limit: Max amount of database rows to return.
        :returns a tuple of (dict of ephemeral keys keyed by DB id, max id), or ({}, None).
        """
        cur = self.sydent.db.cursor()
        res = cur.execute(
            "SELECT id, public_key, verify_count, persistence_ts FROM ephemeral_public_keys"
            " WHERE id > ? AND origin_id IS NULL LIMIT ?",
            (afterId, limit,),
        )
        rows = res.fetchall()

        ephemeral_keys = {}
        maxId = None

        for row in rows:
            maxId, public_key, verify_count, persistence_ts = row
            ephemeral_keys[maxId] = {
                "public_key": public_key,
                "verify_count": verify_count,
                "persistence_ts": persistence_ts,
            }

        return ephemeral_keys, maxId

    def getLastEphemeralPublicKeyIdFromServer(self, server):
        """Returns the last known ephemeral public key that was received from the given server.

        :param server: The name of the origin server.
        :returns the last known DB id received from the given server, or 0 if none.
        """
        cur = self.sydent.db.cursor()
        res = cur.execute(
            "SELECT max(origin_id), count(origin_id) FROM ephemeral_public_keys WHERE origin_server = ?",
            (server,),
        )
        row = res.fetchone()

        if not row or row[1] == 0:
            return 0

        return row[0]

    def getSenderForToken(self, token: str) -> Optional[str]:
        """
        Retrieves the MXID of the user that sent the invite the provided token is for.

        :param token: The token to retrieve the sender of.

        :return: The invite's sender, or None if the token doesn't match an existing
            invite.
        """
        cur = self.sydent.db.cursor()
        res = cur.execute("SELECT sender FROM invite_tokens WHERE token = ?", (token,))
        rows: List[Tuple[str]] = res.fetchall()
        if rows:
            return rows[0][0]
        return None

    def deleteTokens(self, medium: str, address: str) -> None:
        """
        Deletes every token for a given 3PID.

        :param medium: The medium of the 3PID to delete tokens for.
        :param address: The address of the 3PID to delete tokens for.
        """
        cur = self.sydent.db.cursor()

        # Insert a row for every deleted invite in the updated_invites table so the
        # deletion is replicated to other servers.
        res = cur.execute(
            """
            SELECT id, origin_server, origin_id
            FROM invite_tokens WHERE medium = ? AND address = ?
            """,
            (medium, address,),
        )

        rows = res.fetchall()

        cur.executemany(
            """
            INSERT INTO updated_invites (invite_id, origin_server, origin_id)
            VALUES (?, ?, ?)
            """,
            rows,
        )

        # Actually delete the invites.
        cur.execute(
            "DELETE FROM invite_tokens WHERE medium = ? AND address = ?",
            (
                medium,
                address,
            ),
        )

        self.sydent.db.commit()

    def getInviteUpdatesAfterId(self, last_id, limit):
        """Returns every updated token for which its update id is higher than the provided
        `last_id`, capped at `limit` tokens.

        :param last_id: The last ID processed during the previous run.
        :type last_id: int
        :param limit: The maximum number of results to return.
        :type limit: int
        :returns a tuple consisting of a list of invite tokens and the maximum DB id
            that was extracted from the table keeping track of the updates.
            Otherwise returns ([], None) if no tokens are found.
        :rtype: Tuple[List[Dict], int|None]

        """
        cur = self.sydent.db.cursor()

        # Retrieve the IDs of the invites that have been updated since the last time.
        res = cur.execute(
            """
                SELECT u.id, u.invite_id, t.id IS NULL, medium, address, room_id, sender,
                    token, sent_ts, u.origin_server, u.origin_id
                FROM updated_invites AS u
                    LEFT JOIN invite_tokens AS t ON (t.id = u.invite_id)
                WHERE u.id > ? ORDER BY u.id ASC LIMIT ?;
            """,
            (last_id, limit),
        )

        rows = res.fetchall()

        max_id = None

        # Retrieve each invite and append it to a list.
        invites = []
        for row in rows:
            max_id, invite_id, is_deletion, medium, address, room_id, sender, token, sent_ts, origin_server, origin_id = row
            # Append a new dict to the list containing the token's metadata,
            # including an `origin_id` and an `origin_server` so that the receiving end
            # can figure out which invite to update in its local database. If the token
            # originated from this server, use its local ID as the value for
            # `origin_id`, and the local server's server_name for `origin_server`.
            invites.append(
                {
                    "origin_id": origin_id if origin_id is not None else invite_id,
                    "origin_server": origin_server if origin_server is not None else self.sydent.server_name,
                    "medium": medium,
                    "address": address,
                    "room_id": room_id,
                    "sender": sender,
                    "token": token,
                    "sent_ts": sent_ts,
                    "is_deletion": is_deletion,
                }
            )

        self.sydent.db.commit()

        return invites, max_id
