import json
from unittest.mock import Mock, patch

from OpenSSL import crypto
from twisted.internet import defer
from twisted.trial import unittest
from twisted.web.client import Response

from sydent.threepid import ThreepidAssociation
from sydent.threepid.signer import Signer

from tests.utils import make_request, make_sydent


class ReplicationTestCase(unittest.TestCase):
    """Test that a Sydent can correctly replicate data with another Sydent"""

    def setUp(self):
        # Create a new sydent
        self.sydent = make_sydent()

        # Create a fake peer to replicate to.
        peer_public_key_base64 = "+vB8mTaooD/MA8YYZM8t9+vnGhP1937q2icrqPV9JTs"

        # Inject our fake peer into the database.
        cur = self.sydent.db.cursor()
        cur.execute(
            "INSERT INTO peers (name, port, lastSentVersion, active) VALUES (?, ?, ?, ?)",
            ("fake.server", 1234, 0, 1),
        )
        cur.execute(
            "INSERT INTO peer_pubkeys (peername, alg, key) VALUES (?, ?, ?)",
            ("fake.server", "ed25519", peer_public_key_base64),
        )

        self.sydent.db.commit()

        # Build some fake associations.
        self.assocs = []
        assoc_count = 150
        for i in range(assoc_count):
            assoc = ThreepidAssociation(
                medium="email",
                address=f"bob{i}@example.com",
                lookup_hash=None,
                mxid=f"@bob{i}:example.com",
                ts=(i * 10000),
                not_before=0,
                not_after=99999999999,
            )
            self.assocs.append(assoc)

    def test_incoming_replication(self):
        """Impersonate a peer that sends a replication push to Sydent, then checks that it
        accepts the payload and saves it correctly.
        """
        self.sydent.run()

        # Configure the Sydent to impersonate. We need to use "fake.server" as the
        # server's name because that's the name the recipient Sydent has for it. On top
        # of that, the replication servlet expects a TLS certificate in the request so it
        # can extract a common name and figure out which peer sent it from its common
        # name. The common name of the certificate we use for tests is fake.server.
        config = {
            "general": {"server.name": "fake.server"},
            "crypto": {
                "ed25519.signingkey": "ed25519 0 b29eXMMAYCFvFEtq9mLI42aivMtcg4Hl0wK89a+Vb6c"
            },
        }

        fake_sender_sydent = make_sydent(config)
        signer = Signer(fake_sender_sydent)

        # Sign the associations with the Sydent to impersonate so the recipient Sydent
        # can verify the signatures on them.
        signed_assocs = {}
        for assoc_id, assoc in enumerate(self.assocs):
            signed_assoc = signer.signedThreePidAssociation(assoc)
            signed_assocs[assoc_id] = signed_assoc

        # Send the replication push.
        body = {"sgAssocs": signed_assocs}
        request, channel = make_request(
            self.sydent.reactor,
            self.sydent.replicationHttpsServer.factory,
            "POST",
            "/_matrix/identity/replicate/v1/push",
            body,
        )

        self.assertEqual(channel.code, 200)

        # Check that the recipient Sydent has correctly saved the associations in the
        # push.
        cur = self.sydent.db.cursor()
        res = cur.execute("SELECT originId, sgAssoc FROM global_threepid_associations")

        res_assocs = {}
        for row in res.fetchall():
            originId = row[0]
            signed_assoc = json.loads(row[1])

            res_assocs[originId] = signed_assoc

        for assoc_id, signed_assoc in signed_assocs.items():
            self.assertDictEqual(signed_assoc, res_assocs[assoc_id])

    def test_outgoing_replication(self):
        """Make a fake peer and associations and make sure Sydent tries to push to it."""
        cur = self.sydent.db.cursor()

        # Insert the fake associations into the database.
        cur.executemany(
            "INSERT INTO  local_threepid_associations "
            "(medium, address, lookup_hash, mxid, ts, notBefore, notAfter) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    assoc.medium,
                    assoc.address,
                    assoc.lookup_hash,
                    assoc.mxid,
                    assoc.ts,
                    assoc.not_before,
                    assoc.not_after,
                )
                for assoc in self.assocs
            ],
        )

        self.sydent.db.commit()

        # Manually sign all associations so we can check whether Sydent attempted to
        # push the same.
        signer = Signer(self.sydent)
        signed_assocs = {}
        for assoc_id, assoc in enumerate(self.assocs):
            signed_assoc = signer.signedThreePidAssociation(assoc)
            signed_assocs[assoc_id] = signed_assoc

        sent_assocs = {}

        def request(method, uri, headers, body):
            """
            Processes a request sent to the mocked agent.

            :param method: The method of the request.
            :type method: bytes
            :param uri: The URI of the request.
            :type uri: bytes
            :param headers: The headers of the request.
            :type headers: twisted.web.http_headers.Headers
            :param body: The body of the request.
            :type body: twisted.web.client.FileBodyProducer[io.BytesIO]

            :return: A deferred that resolves into a 200 OK response.
            :rtype: twisted.internet.defer.Deferred[Response]
            """
            # Check the method and the URI.
            assert method == b"POST"
            assert uri == b"https://fake.server:1234/_matrix/identity/replicate/v1/push"

            # postJson calls the agent with a BytesIO within a FileBodyProducer, so we
            # need to unpack the payload correctly.
            payload = json.loads(body._inputFile.read().decode("utf8"))
            for assoc_id, assoc in payload["sgAssocs"].items():
                sent_assocs[assoc_id] = assoc

            # Return with a fake response wrapped in a Deferred.
            d = defer.Deferred()
            d.callback(Response((b"HTTP", 1, 1), 200, b"OK", None, None))
            return d

        # Mock the replication client's agent so it runs the custom code instead of
        # actually sending the requests.
        agent = Mock(spec=["request"])
        agent.request.side_effect = request
        self.sydent.replicationHttpsClient.agent = agent

        # Start Sydent and allow some time for all the necessary pushes to happen.
        self.sydent.run()
        self.sydent.reactor.advance(1000)

        # Check that, now that Sydent pushed all the associations it was meant to, we
        # have all of the associations we initially inserted.
        self.assertEqual(len(self.assocs), len(sent_assocs))
        for assoc_id, assoc in sent_assocs.items():
            # Replication payloads use a specific format that causes the JSON encoder to
            # convert the numeric indexes to string, so we need to convert them back when
            # looking up in signed_assocs. Also, the ID of the first association Sydent
            # will push will be 1, so we need to subtract 1 when figuring out which index
            # to lookup.
            self.assertDictEqual(assoc, signed_assocs[int(assoc_id) - 1])


class ReplicationCNTest(unittest.TestCase):
    """Tests for peer certificate CN extraction edge cases in the replication servlet."""

    def setUp(self) -> None:
        self.sydent = make_sydent()

        # Insert a known peer.
        cur = self.sydent.db.cursor()
        cur.execute(
            "INSERT INTO peers (name, port, lastSentVersion, active) VALUES (?, ?, ?, ?)",
            ("fake.server", 1234, 0, 1),
        )
        peer_public_key_base64 = "+vB8mTaooD/MA8YYZM8t9+vnGhP1937q2icrqPV9JTs"
        cur.execute(
            "INSERT INTO peer_pubkeys (peername, alg, key) VALUES (?, ?, ?)",
            ("fake.server", "ed25519", peer_public_key_base64),
        )
        self.sydent.db.commit()

    def test_known_peer_cn_accepted(self) -> None:
        """A peer cert with CN matching a known peer is accepted (existing test validates this,
        but let's have a focused unit-level check)."""
        self.sydent.run()

        # The FakeChannel.getPeerCertificate() returns a cert with CN=fake.server,
        # and we inserted fake.server as a peer. A valid request should be accepted.
        body = {"sgAssocs": {}}
        request, channel = make_request(
            self.sydent.reactor,
            self.sydent.replicationHttpsServer.factory,
            "POST",
            "/_matrix/identity/replicate/v1/push",
            body,
        )
        self.assertEqual(channel.code, 200)

    def test_unknown_peer_cn_rejected(self) -> None:
        """A peer cert with CN that doesn't match any known peer returns 403."""
        self.sydent.run()

        # Generate a cert with a CN that is NOT in the peers table.
        unknown_key = crypto.PKey()
        unknown_key.generate_key(crypto.TYPE_RSA, 2048)
        unknown_cert = crypto.X509()
        unknown_cert.get_subject().CN = "unknown.server"
        unknown_cert.set_serial_number(1000)
        unknown_cert.gmtime_adj_notBefore(0)
        unknown_cert.gmtime_adj_notAfter(10 * 365 * 24 * 60 * 60)
        unknown_cert.set_issuer(unknown_cert.get_subject())
        unknown_cert.set_pubkey(unknown_key)
        unknown_cert.sign(unknown_key, "sha256")

        # Patch FakeChannel.getPeerCertificate to return our unknown cert.
        with patch(
            "tests.utils.FakeChannel.getPeerCertificate", return_value=unknown_cert
        ):
            body = {"sgAssocs": {}}
            request, channel = make_request(
                self.sydent.reactor,
                self.sydent.replicationHttpsServer.factory,
                "POST",
                "/_matrix/identity/replicate/v1/push",
                body,
            )
        self.assertEqual(channel.code, 403)
        self.assertEqual(channel.json_body["errcode"], "M_UNKNOWN_PEER")

    def test_no_cn_rejected(self) -> None:
        """A peer cert with no commonName returns 403."""
        self.sydent.run()

        # Generate a cert with no CN set.
        no_cn_key = crypto.PKey()
        no_cn_key.generate_key(crypto.TYPE_RSA, 2048)
        no_cn_cert = crypto.X509()
        # Don't set CN — leave subject empty.
        no_cn_cert.set_serial_number(2000)
        no_cn_cert.gmtime_adj_notBefore(0)
        no_cn_cert.gmtime_adj_notAfter(10 * 365 * 24 * 60 * 60)
        no_cn_cert.set_issuer(no_cn_cert.get_subject())
        no_cn_cert.set_pubkey(no_cn_key)
        no_cn_cert.sign(no_cn_key, "sha256")

        with patch(
            "tests.utils.FakeChannel.getPeerCertificate", return_value=no_cn_cert
        ):
            body = {"sgAssocs": {}}
            request, channel = make_request(
                self.sydent.reactor,
                self.sydent.replicationHttpsServer.factory,
                "POST",
                "/_matrix/identity/replicate/v1/push",
                body,
            )
        self.assertEqual(channel.code, 403)
        self.assertEqual(channel.json_body["errcode"], "M_UNKNOWN_PEER")
