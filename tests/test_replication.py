import json
import ssl
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from OpenSSL import crypto

from sydent.threepid import ThreepidAssociation
from sydent.threepid.signer import Signer

from tests.utils import FAKE_SERVER_CERT_PEM, make_sydent


@pytest.fixture
def sydent():
    """Create a Sydent instance with a fake peer configured."""
    sydent = make_sydent()

    # Create a fake peer to replicate to.
    peer_public_key_base64 = "+vB8mTaooD/MA8YYZM8t9+vnGhP1937q2icrqPV9JTs"

    # Inject our fake peer into the database.
    cur = sydent.db.cursor()
    cur.execute(
        "INSERT INTO peers (name, port, lastSentVersion, active) VALUES (?, ?, ?, ?)",
        ("fake.server", 1234, 0, 1),
    )
    cur.execute(
        "INSERT INTO peer_pubkeys (peername, alg, key) VALUES (?, ?, ?)",
        ("fake.server", "ed25519", peer_public_key_base64),
    )
    sydent.db.commit()

    return sydent


@pytest.fixture
def assocs():
    """Build some fake associations."""
    assocs = []
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
        assocs.append(assoc)
    return assocs


def _make_fake_ssl_object():
    """Create a fake SSL object that returns our test certificate."""
    cert = crypto.load_certificate(crypto.FILETYPE_PEM, FAKE_SERVER_CERT_PEM)
    # Convert to DER for ssl module compatibility
    der_bytes = crypto.dump_certificate(crypto.FILETYPE_ASN1, cert)

    ssl_object = MagicMock()
    ssl_object.getpeercert.return_value = ssl.DER_cert_to_PEM_cert(der_bytes)
    # For the OpenSSL X509 extraction path
    ssl_object.get_channel_binding = MagicMock(return_value=None)
    return ssl_object, cert


@pytest.fixture
async def replication_client(sydent):
    """Create a test client for the replication API, with mocked peer cert."""
    app = sydent.replicationHttpsServer.app

    # We need to mock the transport's get_extra_info to return a fake SSL object
    # so the replication servlet can extract the peer certificate.
    fake_ssl_object, fake_cert = _make_fake_ssl_object()

    async with TestClient(TestServer(app)) as client:
        # Patch the request to inject SSL peer certificate info
        original_request = client.session.request

        async def patched_request(*args, **kwargs):
            response = await original_request(*args, **kwargs)
            return response

        client._fake_cert = fake_cert
        yield client


async def test_incoming_replication(sydent, assocs):
    """Impersonate a peer that sends a replication push to Sydent, then checks that it
    accepts the payload and saves it correctly.
    """
    # Configure the Sydent to impersonate.
    config = {
        "general": {"server.name": "fake.server"},
        "crypto": {
            "ed25519.signingkey": "ed25519 0 b29eXMMAYCFvFEtq9mLI42aivMtcg4Hl0wK89a+Vb6c"
        },
    }

    fake_sender_sydent = make_sydent(config)
    signer = Signer(fake_sender_sydent)

    # Sign the associations with the Sydent to impersonate.
    signed_assocs = {}
    for assoc_id, assoc in enumerate(assocs):
        signed_assoc = signer.signedThreePidAssociation(assoc)
        signed_assocs[assoc_id] = signed_assoc

    # Send the replication push via the test client.
    body = {"sgAssocs": signed_assocs}

    app = sydent.replicationHttpsServer.app

    # Mock the CN extraction to return "fake.server" (matching our peer name).
    with patch(
        "sydent.http.servlets.replication._get_peer_certificate_cn",
        return_value="fake.server",
    ):
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/_matrix/identity/replicate/v1/push",
                json=body,
            )

    assert resp.status == 200

    # Check that the recipient Sydent has correctly saved the associations.
    cur = sydent.db.cursor()
    res = cur.execute("SELECT originId, sgAssoc FROM global_threepid_associations")

    res_assocs = {}
    for row in res.fetchall():
        originId = row[0]
        signed_assoc = json.loads(row[1])
        res_assocs[originId] = signed_assoc

    for assoc_id, signed_assoc in signed_assocs.items():
        assert signed_assoc == res_assocs[assoc_id]


async def test_outgoing_replication(sydent, assocs):
    """Make a fake peer and associations and make sure Sydent tries to push to it."""
    cur = sydent.db.cursor()

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
            for assoc in assocs
        ],
    )
    sydent.db.commit()

    # Manually sign all associations so we can check whether Sydent attempted
    # to push the same.
    signer = Signer(sydent)
    signed_assocs = {}
    for assoc_id, assoc in enumerate(assocs):
        signed_assoc = signer.signedThreePidAssociation(assoc)
        signed_assocs[assoc_id] = signed_assoc

    sent_assocs = {}

    async def mock_post_json(uri, body, **kwargs):
        """Mock the federation HTTP client's post method."""
        assert "/_matrix/identity/replicate/v1/push" in uri
        for assoc_id, assoc in body["sgAssocs"].items():
            sent_assocs[assoc_id] = assoc
        mock_resp = MagicMock()
        mock_resp.status = 200
        return mock_resp

    # Mock the replication client's post method.
    with patch.object(
        sydent.replicationHttpsClient,
        "postJson",
        side_effect=mock_post_json,
    ):
        # Multiple pushes may be needed due to ASSOCIATIONS_PUSH_LIMIT (100).
        for _ in range(5):
            await sydent.pusher.scheduledPush()

    # Check that Sydent pushed all the associations.
    assert len(assocs) == len(sent_assocs)
    for assoc_id, assoc in sent_assocs.items():
        assert assoc == signed_assocs[int(assoc_id) - 1]


# --- CN extraction edge case tests ---


async def test_known_peer_cn_accepted(sydent):
    """A peer cert with CN matching a known peer is accepted."""
    app = sydent.replicationHttpsServer.app
    body = {"sgAssocs": {}}

    with patch(
        "sydent.http.servlets.replication._get_peer_certificate_cn",
        return_value="fake.server",
    ):
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/_matrix/identity/replicate/v1/push",
                json=body,
            )
    assert resp.status == 200


async def test_unknown_peer_cn_rejected(sydent):
    """A peer cert with CN that doesn't match any known peer returns 403."""
    app = sydent.replicationHttpsServer.app
    body = {"sgAssocs": {}}

    with patch(
        "sydent.http.servlets.replication._get_peer_certificate_cn",
        return_value="unknown.server",
    ):
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/_matrix/identity/replicate/v1/push",
                json=body,
            )
            resp_body = await resp.json()
    assert resp.status == 403
    assert resp_body["errcode"] == "M_UNKNOWN_PEER"


async def test_no_cn_rejected(sydent):
    """No TLS certificate returns 403."""
    app = sydent.replicationHttpsServer.app
    body = {"sgAssocs": {}}

    # Don't patch _get_peer_certificate_cn — the default behavior with no
    # SSL transport should raise.
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/_matrix/identity/replicate/v1/push",
            json=body,
        )
        resp_body = await resp.json()
    assert resp.status == 403
    assert resp_body["errcode"] == "M_UNKNOWN_PEER"
