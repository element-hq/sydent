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


class V1Test(BaseApiTest, unittest.TestCase):
    API_VERSION = "v1"

    def test_bulk_lookup(self):
        params = self.api.request_and_submit_email_code("thing1@nowhere.test")
        body = self.api.bind_email(
            params["sid"], params["client_secret"], "@thing1:fake.test"
        )

        params = self.api.request_and_submit_email_code("thing2@nowhere.test")
        body = self.api.bind_email(
            params["sid"], params["client_secret"], "@thing2:fake.test"
        )

        body = self.api.bulk_lookup(
            [
                ("email", "thing1@nowhere.test"),
                ("email", "thing2@nowhere.test"),
                ("email", "thing3@nowhere.test"),
            ]
        )

        self.assertIn(
            ["email", "thing1@nowhere.test", "@thing1:fake.test"], body["threepids"]
        )
        self.assertIn(
            ["email", "thing2@nowhere.test", "@thing2:fake.test"], body["threepids"]
        )
        self.assertEquals(len(body["threepids"]), 2)

    def test_bind_and_lookup(self):
        params = self.api.request_and_submit_email_code("fakeemail3@nowhere.test")
        body = self.api.bind_email(
            params["sid"], params["client_secret"], "@some_mxid:fake.test"
        )

        self.assertEquals(body["medium"], "email")
        self.assertEquals(body["address"], "fakeemail3@nowhere.test")
        self.assertEquals(body["mxid"], "@some_mxid:fake.test")

        body2 = self.api.lookupv1("email", "fakeemail3@nowhere.test")

        self.assertEquals(body2["medium"], "email")
        self.assertEquals(body2["address"], "fakeemail3@nowhere.test")
        self.assertEquals(body2["mxid"], "@some_mxid:fake.test")

        self.assertEquals(body["ts"], body2["ts"])
        self.assertEquals(body["not_before"], body2["not_before"])
        self.assertEquals(body["not_after"], body2["not_after"])


if __name__ == "__main__":
    import sys

    from twisted.python import log

    log.startLogging(sys.stdout)
    unittest.main()
