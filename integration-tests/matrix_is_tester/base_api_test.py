#!/usr/bin/env python

# -*- coding: utf-8 -*-

# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json

# These are standard python unit tests, but are generally intended
# to be run with trial. Trial doesn't capture logging nicely if you
# use python 'logging': it only works if you use Twisted's own.
from twisted.python import log

from matrix_is_tester.is_api import IsApi
from matrix_is_tester.launch_is import get_or_launch_is
from matrix_is_tester.mailsink import get_shared_mailsink


class BaseApiTest(object):
    """
    Not a test case itself, but can be subclassed to test APIs common
    between versions.
    """

    def setUp(self):
        self.baseUrl = get_or_launch_is()

        self.mailSink = get_shared_mailsink()

        self.api = IsApi(self.baseUrl, self.API_VERSION, self.mailSink)

    def test_ping(self):
        body = self.api.ping()
        self.assertEquals(body, {})

    def test_request_email_code(self):
        body = self.api.request_email_code("fakeemail1@nowhere.test", "sekrit", 1)
        log.msg("Got response %r" % (body,))
        self.assertIn("sid", body)
        self.mailSink.get_mail()

    def test_reject_invalid_email(self):
        body = self.api.request_email_code(
            "fakeemail1@nowhere.test@elsewhere.test", "sekrit", 1
        )
        self.assertEquals(body["errcode"], "M_INVALID_EMAIL")

    def test_submit_email_code(self):
        self.api.request_and_submit_email_code("fakeemail2@nowhere.test")

    def test_submit_email_code_get(self):
        req_response = self.api.request_email_code(
            "steve@nowhere.test", "verysekrit", 1
        )
        sid = req_response["sid"]

        token = self.api.get_token_from_mail()

        body = self.api.submit_email_token_via_get(sid, "verysekrit", token)
        self.assertEquals(body, b"matrix_is_tester:email_submit_get_response\n")

        body = self.api.get_validated_threepid(sid, "verysekrit")

        self.assertEquals(body["medium"], "email")
        self.assertEquals(body["address"], "steve@nowhere.test")

    def test_unverified_bind(self):
        req_code_body = self.api.request_email_code(
            "fakeemail5@nowhere.test", "sekrit", 1
        )
        # get the mail so we don't leave it in the queue
        self.mailSink.get_mail()
        body = self.api.bind_email(
            req_code_body["sid"], "sekrit", "@commonapitests:127.0.0.1:4490"
        )
        self.assertEquals(body["errcode"], "M_SESSION_NOT_VALIDATED")

    def test_get_validated_threepid(self):
        params = self.api.request_and_submit_email_code("fakeemail4@nowhere.test")

        body = self.api.get_validated_threepid(params["sid"], params["client_secret"])

        self.assertEquals(body["medium"], "email")
        self.assertEquals(body["address"], "fakeemail4@nowhere.test")

    def test_get_validated_threepid_not_validated(self):
        req_code_body = self.api.request_email_code(
            "fakeemail5@nowhere.test", "sekrit", 1
        )
        # get the mail, otherwise the next test will get it
        # instead of the one it was expecting
        self.mailSink.get_mail()

        get_val_body = self.api.get_validated_threepid(req_code_body["sid"], "sekrit")
        self.assertEquals(get_val_body["errcode"], "M_SESSION_NOT_VALIDATED")

    def test_store_invite(self):
        body = self.api.store_invite(
            {
                "medium": "email",
                "address": "ian@fake.test",
                "room_id": "$aroom:fake.test",
                "sender": "@commonapitests:127.0.0.1:4490",
                "room_alias": "#alias:fake.test",
                "room_avatar_url": "mxc://fake.test/roomavatar",
                "room_name": "my excellent room",
                "sender_display_name": "Ian Sender",
                "sender_avatar_url": "mxc://fake.test/iansavatar",
            }
        )
        self.assertGreater(len(body["token"]), 0)
        # must be redacted
        self.assertNotEqual(body["display_name"], "ian@fake.test")
        self.assertGreater(len(body["public_keys"]), 0)

        for k in body["public_keys"]:
            is_valid_body = self.api.pubkey_is_valid(
                k["key_validity_url"], k["public_key"]
            )
            self.assertTrue(is_valid_body["valid"])

        mail = self.mailSink.get_mail()
        log.msg("Got email (invite): %r" % (mail,))
        mail_object = json.loads(mail["data"])
        self.assertEquals(mail_object["token"], body["token"])
        self.assertEquals(mail_object["room_alias"], "#alias:fake.test")
        self.assertEquals(mail_object["room_avatar_url"], "mxc://fake.test/roomavatar")
        self.assertEquals(mail_object["room_name"], "my excellent room")
        self.assertEquals(mail_object["sender_display_name"], "Ian Sender")
        self.assertEquals(
            mail_object["sender_avatar_url"], "mxc://fake.test/iansavatar"
        )

    def test_store_invite_bound_threepid(self):
        params = self.api.request_and_submit_email_code("already_here@fake.test")
        self.api.bind_email(
            params["sid"], params["client_secret"], "@some_mxid:fake.test"
        )

        body = self.api.store_invite(
            {
                "medium": "email",
                "address": "already_here@fake.test",
                "room_id": "$aroom:fake.test",
                "sender": "@commonapitests:127.0.0.1:4490",
            }
        )
        self.assertEquals(body["errcode"], "M_THREEPID_IN_USE")
