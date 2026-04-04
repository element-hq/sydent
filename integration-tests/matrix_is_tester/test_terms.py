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

from matrix_is_tester.fakehs import get_shared_fake_hs
from matrix_is_tester.is_api import IsApi
from matrix_is_tester.launch_is import get_or_launch_is


class TermsTest(unittest.TestCase):
    def setUp(self):
        self.fakeHs = get_shared_fake_hs()
        self.fakeHsAddr = self.fakeHs.get_addr()

    def test_get_terms(self):
        base_url = get_or_launch_is(True)
        api = IsApi(base_url, "v2", None)

        body = api.get_terms()
        self.assertIn("policies", body)
        self.assertIn("privacy_policy", body["policies"])
        self.assertEquals(body["policies"]["privacy_policy"]["version"], "1.2")
        self.assertIn("en", body["policies"]["privacy_policy"])
        self.assertIn("name", body["policies"]["privacy_policy"]["en"])
        self.assertIn("url", body["policies"]["privacy_policy"]["en"])
        self.assertIn("fr", body["policies"]["privacy_policy"])
        self.assertIn("name", body["policies"]["privacy_policy"]["fr"])
        self.assertIn("url", body["policies"]["privacy_policy"]["fr"])

        self.assertIn("terms_of_service", body["policies"])
        self.assertEquals(body["policies"]["terms_of_service"]["version"], "5.0")
        self.assertIn("en", body["policies"]["terms_of_service"])
        self.assertIn("name", body["policies"]["terms_of_service"]["en"])
        self.assertIn("url", body["policies"]["terms_of_service"]["en"])
        self.assertIn("fr", body["policies"]["terms_of_service"])
        self.assertIn("name", body["policies"]["terms_of_service"]["fr"])
        self.assertIn("url", body["policies"]["terms_of_service"]["fr"])

    def test_agree_to_terms(self):
        base_url = get_or_launch_is(True)
        api = IsApi(base_url, "v2", None)
        api.make_account(self.fakeHsAddr)

        terms_body = api.get_terms()
        agree_body = api.agree_to_terms(
            [terms_body["policies"]["privacy_policy"]["en"]["url"]]
        )
        self.assertEqual(agree_body, {})

    def test_reject_if_not_authed(self):
        base_url = get_or_launch_is(True)
        api = IsApi(base_url, "v2", None)

        err = api.check_terms_signed()
        self.assertEquals(err["errcode"], "M_UNAUTHORIZED")

    def test_terms_reject_if_none_agreed(self):
        base_url = get_or_launch_is(True)
        api = IsApi(base_url, "v2", None)
        api.make_account(self.fakeHsAddr)

        err = api.check_terms_signed()
        self.assertEquals(err["errcode"], "M_TERMS_NOT_SIGNED")

    def test_terms_reject_if_not_all_agreed(self):
        base_url = get_or_launch_is(True)
        api = IsApi(base_url, "v2", None)
        api.make_account(self.fakeHsAddr)

        terms_body = api.get_terms()
        api.agree_to_terms([terms_body["policies"]["privacy_policy"]["en"]["url"]])

        err = api.check_terms_signed()
        self.assertEquals(err["errcode"], "M_TERMS_NOT_SIGNED")

    def test_terms_allow_when_all_agreed(self):
        base_url = get_or_launch_is(True)
        api = IsApi(base_url, "v2", None)
        api.make_account(self.fakeHsAddr)

        terms_body = api.get_terms()
        api.agree_to_terms(
            [
                terms_body["policies"]["privacy_policy"]["en"]["url"],
                terms_body["policies"]["terms_of_service"]["en"]["url"],
            ]
        )

        err = api.check_terms_signed()
        self.assertIsNone(err)

    def test_terms_allow_mixed_langs(self):
        base_url = get_or_launch_is(True)
        api = IsApi(base_url, "v2", None)
        api.make_account(self.fakeHsAddr)

        terms_body = api.get_terms()
        api.agree_to_terms(
            [
                terms_body["policies"]["privacy_policy"]["en"]["url"],
                terms_body["policies"]["terms_of_service"]["fr"]["url"],
            ]
        )

        err = api.check_terms_signed()
        self.assertIsNone(err)

    def test_terms_allow_in_separate_calls(self):
        base_url = get_or_launch_is(True)
        api = IsApi(base_url, "v2", None)
        api.make_account(self.fakeHsAddr)

        terms_body = api.get_terms()
        api.agree_to_terms([terms_body["policies"]["privacy_policy"]["en"]["url"]])
        api.agree_to_terms([terms_body["policies"]["terms_of_service"]["en"]["url"]])

        err = api.check_terms_signed()
        self.assertIsNone(err)

    def test_terms_no_terms(self):
        base_url = get_or_launch_is(False)
        api = IsApi(base_url, "v2", None)
        api.make_account(self.fakeHsAddr)

        terms_body = api.get_terms()
        self.assertEquals(terms_body["policies"], {})

    def test_terms_allow_if_no_terms(self):
        base_url = get_or_launch_is(False)
        api = IsApi(base_url, "v2", None)
        api.make_account(self.fakeHsAddr)

        err = api.check_terms_signed()
        self.assertIsNone(err)


if __name__ == "__main__":
    import sys

    from twisted.python import log

    log.startLogging(sys.stdout)
    unittest.main()
