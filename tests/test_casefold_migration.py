# Copyright 2025 New Vector Ltd.
# Copyright 2021 Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import json
from unittest.mock import patch

import pytest

from scripts.casefold_db import (
    calculate_lookup_hash,
    update_global_associations,
    update_local_associations,
)
from sydent.util import json_decoder
from sydent.util.emailutils import sendEmail

from tests.utils import make_sydent


def create_signedassoc(medium, address, mxid, ts, not_before, not_after):
    return {
        "medium": medium,
        "address": address,
        "mxid": mxid,
        "ts": ts,
        "not_before": not_before,
        "not_after": not_after,
    }


@pytest.fixture
def sydent_with_associations():
    """Create a Sydent with local and global associations for migration testing."""
    sydent = make_sydent()

    # create some local associations
    associations = []

    for i in range(10):
        address = f"bob{i}@example.com"
        associations.append(
            {
                "medium": "email",
                "address": address,
                "lookup_hash": calculate_lookup_hash(sydent, address),
                "mxid": f"@bob{i}:example.com",
                "ts": (i * 10000),
                "not_before": 0,
                "not_after": 99999999999,
            }
        )
    # create some casefold-conflicting associations
    for i in range(5):
        address = f"BOB{i}@example.com"
        associations.append(
            {
                "medium": "email",
                "address": address,
                "lookup_hash": calculate_lookup_hash(sydent, address),
                "mxid": f"@otherbob{i}:example.com",
                "ts": (i * 10000),
                "not_before": 0,
                "not_after": 99999999999,
            }
        )

    associations.append(
        {
            "medium": "email",
            "address": "BoB4@example.com",
            "lookup_hash": calculate_lookup_hash(sydent, "BoB4@example.com"),
            "mxid": "@otherbob4:example.com",
            "ts": 42000,
            "not_before": 0,
            "not_after": 99999999999,
        }
    )

    # add all associations to db
    cur = sydent.db.cursor()

    cur.executemany(
        "INSERT INTO  local_threepid_associations "
        "(medium, address, lookup_hash, mxid, ts, notBefore, notAfter) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                assoc["medium"],
                assoc["address"],
                assoc["lookup_hash"],
                assoc["mxid"],
                assoc["ts"],
                assoc["not_before"],
                assoc["not_after"],
            )
            for assoc in associations
        ],
    )

    sydent.db.commit()

    # create some global associations
    associations = []
    originServer = sydent.config.general.server_name

    for i in range(10):
        address = f"bob{i}@example.com"
        mxid = f"@bob{i}:example.com"
        ts = 10000 * i
        associations.append(
            {
                "medium": "email",
                "address": address,
                "lookup_hash": calculate_lookup_hash(sydent, address),
                "mxid": mxid,
                "ts": ts,
                "not_before": 0,
                "not_after": 99999999999,
                "originServer": originServer,
                "originId": i,
                "sgAssoc": json.dumps(
                    create_signedassoc("email", address, mxid, ts, 0, 99999999999)
                ),
            }
        )
    # create some casefold-conflicting associations
    for i in range(5):
        address = f"BOB{i}@example.com"
        mxid = f"@BOB{i}:example.com"
        ts = 10000 * i
        associations.append(
            {
                "medium": "email",
                "address": address,
                "lookup_hash": calculate_lookup_hash(sydent, address),
                "mxid": mxid,
                "ts": ts + 1,
                "not_before": 0,
                "not_after": 99999999999,
                "originServer": originServer,
                "originId": i + 10,
                "sgAssoc": json.dumps(
                    create_signedassoc("email", address, mxid, ts, 0, 99999999999)
                ),
            }
        )

    # add all associations to db
    cur = sydent.db.cursor()

    cur.executemany(
        "INSERT INTO global_threepid_associations "
        "(medium, address, lookup_hash, mxid, ts, notBefore, notAfter, originServer, originId, sgAssoc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                assoc["medium"],
                assoc["address"],
                assoc["lookup_hash"],
                assoc["mxid"],
                assoc["ts"],
                assoc["not_before"],
                assoc["not_after"],
                assoc["originServer"],
                assoc["originId"],
                assoc["sgAssoc"],
            )
            for assoc in associations
        ],
    )

    sydent.db.commit()

    return sydent


def test_migration_email(sydent_with_associations):
    sydent = sydent_with_associations

    with patch("sydent.util.emailutils.smtplib") as smtplib:
        # self.sydent.config.email.template is deprecated
        if sydent.config.email.template is None:
            templateFile = sydent.get_branded_template(
                None,
                "migration_template.eml",
            )
        else:
            templateFile = sydent.config.email.template

        sendEmail(
            sydent,
            templateFile,
            "bob@example.com",
            {
                "mxid": "@bob:example.com",
                "subject_header_value": "MatrixID Deletion",
            },
        )
        smtp = smtplib.SMTP.return_value
        email_contents = smtp.sendmail.call_args[0][2].decode("utf-8")
        assert "In the past" in email_contents

        # test email was sent
        smtp.sendmail.assert_called()


def test_local_db_migration(sydent_with_associations):
    sydent = sydent_with_associations

    with patch("sydent.util.emailutils.smtplib") as smtplib:
        update_local_associations(
            sydent,
            sydent.db,
            send_email=True,
            dry_run=False,
            test=True,
        )

    # test 5 emails were sent
    smtp = smtplib.SMTP.return_value
    assert smtp.sendmail.call_count == 5

    # don't send emails to people who weren't affected
    assert [
        "bob5@example.com",
        "bob6@example.com",
        "bob7@example.com",
        "bob8@example.com",
        "bob9@example.com",
    ] not in smtp.sendmail.call_args_list

    # make sure someone who is affected gets email
    assert "bob4@example.com" in smtp.sendmail.call_args_list[0][0]

    cur = sydent.db.cursor()
    res = cur.execute("SELECT * FROM local_threepid_associations")

    db_state = res.fetchall()

    # five addresses should have been deleted
    assert len(db_state) == 10

    # iterate through db and make sure all addresses are casefolded and hash matches casefolded address
    for row in db_state:
        casefolded = row[2].casefold()
        assert row[2] == casefolded
        assert calculate_lookup_hash(sydent, row[2]) == calculate_lookup_hash(
            sydent, casefolded
        )


def test_global_db_migration(sydent_with_associations):
    sydent = sydent_with_associations

    update_global_associations(
        sydent,
        sydent.db,
        dry_run=False,
    )

    cur = sydent.db.cursor()
    res = cur.execute("SELECT * FROM global_threepid_associations")

    db_state = res.fetchall()

    # five addresses should have been deleted
    assert len(db_state) == 10

    # iterate through db and make sure all addresses are casefolded and hash matches casefolded address
    # and make sure the casefolded address matches the address in sgAssoc
    for row in db_state:
        casefolded = row[2].casefold()
        assert row[2] == casefolded
        assert calculate_lookup_hash(sydent, row[2]) == calculate_lookup_hash(
            sydent, casefolded
        )
        sgassoc = json_decoder.decode(row[9])
        assert row[2] == sgassoc["address"]


def test_local_no_email_does_not_send_email(sydent_with_associations):
    sydent = sydent_with_associations

    with patch("sydent.util.emailutils.smtplib") as smtplib:
        update_local_associations(
            sydent,
            sydent.db,
            send_email=False,
            dry_run=False,
            test=True,
        )
        smtp = smtplib.SMTP.return_value

        # test no emails were sent
        assert smtp.sendmail.call_count == 0


def test_dry_run_does_nothing():
    sydent = make_sydent()

    # Populate the database (duplicating fixture logic for fresh state)
    associations = []
    for i in range(10):
        address = f"bob{i}@example.com"
        associations.append(
            {
                "medium": "email",
                "address": address,
                "lookup_hash": calculate_lookup_hash(sydent, address),
                "mxid": f"@bob{i}:example.com",
                "ts": (i * 10000),
                "not_before": 0,
                "not_after": 99999999999,
            }
        )
    for i in range(5):
        address = f"BOB{i}@example.com"
        associations.append(
            {
                "medium": "email",
                "address": address,
                "lookup_hash": calculate_lookup_hash(sydent, address),
                "mxid": f"@otherbob{i}:example.com",
                "ts": (i * 10000),
                "not_before": 0,
                "not_after": 99999999999,
            }
        )

    cur = sydent.db.cursor()
    cur.executemany(
        "INSERT INTO  local_threepid_associations "
        "(medium, address, lookup_hash, mxid, ts, notBefore, notAfter) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                a["medium"],
                a["address"],
                a["lookup_hash"],
                a["mxid"],
                a["ts"],
                a["not_before"],
                a["not_after"],
            )
            for a in associations
        ],
    )
    sydent.db.commit()

    originServer = sydent.config.general.server_name
    global_associations = []
    for i in range(10):
        address = f"bob{i}@example.com"
        mxid = f"@bob{i}:example.com"
        ts = 10000 * i
        global_associations.append(
            {
                "medium": "email",
                "address": address,
                "lookup_hash": calculate_lookup_hash(sydent, address),
                "mxid": mxid,
                "ts": ts,
                "not_before": 0,
                "not_after": 99999999999,
                "originServer": originServer,
                "originId": i,
                "sgAssoc": json.dumps(
                    create_signedassoc("email", address, mxid, ts, 0, 99999999999)
                ),
            }
        )
    for i in range(5):
        address = f"BOB{i}@example.com"
        mxid = f"@BOB{i}:example.com"
        ts = 10000 * i
        global_associations.append(
            {
                "medium": "email",
                "address": address,
                "lookup_hash": calculate_lookup_hash(sydent, address),
                "mxid": mxid,
                "ts": ts + 1,
                "not_before": 0,
                "not_after": 99999999999,
                "originServer": originServer,
                "originId": i + 10,
                "sgAssoc": json.dumps(
                    create_signedassoc("email", address, mxid, ts, 0, 99999999999)
                ),
            }
        )

    cur.executemany(
        "INSERT INTO global_threepid_associations "
        "(medium, address, lookup_hash, mxid, ts, notBefore, notAfter, originServer, originId, sgAssoc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                a["medium"],
                a["address"],
                a["lookup_hash"],
                a["mxid"],
                a["ts"],
                a["not_before"],
                a["not_after"],
                a["originServer"],
                a["originId"],
                a["sgAssoc"],
            )
            for a in global_associations
        ],
    )
    sydent.db.commit()

    # grab a snapshot of global table before running script
    res1 = cur.execute("SELECT mxid FROM global_threepid_associations")
    list1 = res1.fetchall()

    with patch("sydent.util.emailutils.smtplib") as smtplib:
        update_global_associations(
            sydent,
            sydent.db,
            dry_run=True,
        )

    # test no emails were sent
    smtp = smtplib.SMTP.return_value
    assert smtp.sendmail.call_count == 0

    res2 = cur.execute("SELECT mxid FROM global_threepid_associations")
    list2 = res2.fetchall()

    assert list1 == list2

    # grab a snapshot of local table db before running script
    res3 = cur.execute("SELECT mxid FROM local_threepid_associations")
    list3 = res3.fetchall()

    with patch("sydent.util.emailutils.smtplib") as smtplib:
        update_local_associations(
            sydent,
            sydent.db,
            send_email=True,
            dry_run=True,
            test=True,
        )

    # test no emails were sent
    smtp = smtplib.SMTP.return_value
    assert smtp.sendmail.call_count == 0

    res4 = cur.execute("SELECT mxid FROM local_threepid_associations")
    list4 = res4.fetchall()
    assert list3 == list4
