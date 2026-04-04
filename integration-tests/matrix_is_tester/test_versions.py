#!/usr/bin/env python

# -*- coding: utf-8 -*-

# Copyright 2022 The Matrix.org Foundation C.I.C.
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

from matrix_is_tester.is_api import IsApi
from matrix_is_tester.launch_is import get_or_launch_is
from matrix_is_tester.mailsink import get_shared_mailsink


class VersionsTest(unittest.TestCase):
    def setUp(self):
        baseUrl = get_or_launch_is()
        mailSink = get_shared_mailsink()

        # The API version doesn't matter.
        self.api = IsApi(baseUrl, "v1", mailSink)

    def test_versions(self):
        body = self.api.get_versions()

        self.assertIn("versions", body)
        self.assertIn(["v1.1"], body["versions"])


if __name__ == "__main__":
    import sys

    from twisted.python import log

    log.startLogging(sys.stdout)
    unittest.main()
