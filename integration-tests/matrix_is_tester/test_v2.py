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

import unittest

from matrix_is_tester.base_api_test import BaseApiTest
from matrix_is_tester.fakehs import get_shared_fake_hs, token_for_user


class V2Test(BaseApiTest, unittest.TestCase):
    API_VERSION = "v2"

    def setUp(self):
        super(V2Test, self).setUp()

        self.fakeHs = get_shared_fake_hs()
        self.api.make_account(
            self.fakeHs.get_addr(), token_for_user("@commonapitests:127.0.0.1:4490")
        )

    def test_bind_and_lookup(self):
        params = self.api.request_and_submit_email_code("fakeemail3@nowhere.test")
        body = self.api.bind_email(
            params["sid"], params["client_secret"], "@commonapitests:127.0.0.1:4490"
        )

        self.assertEquals(body["medium"], "email")
        self.assertEquals(body["address"], "fakeemail3@nowhere.test")
        self.assertEquals(body["mxid"], "@commonapitests:127.0.0.1:4490")

        hash_details = self.api.hash_details()

        lookup_str = "%s %s" % ("fakeemail3@nowhere.test", "email")
        body2 = self.api.hashed_lookup(
            [lookup_str], "none", hash_details["lookup_pepper"]
        )

        self.assertIn(lookup_str, body2["mappings"])
        self.assertEquals(
            body2["mappings"][lookup_str], "@commonapitests:127.0.0.1:4490"
        )


if __name__ == "__main__":
    import sys

    from twisted.python import log

    log.startLogging(sys.stdout)
    unittest.main()
