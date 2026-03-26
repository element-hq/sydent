# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import os
import shutil
import tempfile
import time
from subprocess import Popen

CFG_TEMPLATE = """
[http]
clientapi.http.bind_address = localhost
clientapi.http.port = {port}
client_http_base = http://localhost:{port}
federation.verifycerts = False

[db]
db.file = :memory:

[general]
server.name = test.local
terms.path = {terms_path}
info_path = {info_path}
templates.path = {testsubject_path}/res
brand.default = is-test


ip.whitelist = 127.0.0.1

[email]
email.tlsmode = 0
email.invite.subject = %(sender_display_name)s has invited you to chat
email.invite.subject_space = %(sender_display_name)s has invited you to a space
email.smtphost = localhost
email.from = Sydent Validation <noreply@localhost>
email.smtpport = 9925
email.subject = Your Validation Token
email.ratelimit_sender.burst = 100000
email.ratelimit_sender.rate_hz = 100000
"""


class MatrixIsTestLauncher:
    def __init__(self, with_terms):
        self.with_terms = with_terms

    def launch(self):
        sydent_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
            )
        )
        testsubject_path = os.path.join(
            sydent_path,
            "matrix_is_test",
        )
        terms_path = (
            os.path.join(testsubject_path, "terms.yaml") if self.with_terms else ""
        )
        info_path = os.path.join(testsubject_path, "info.yaml")
        port = 8099 if self.with_terms else 8098

        self.tmpdir = tempfile.mkdtemp(prefix="sydenttest")

        with open(os.path.join(self.tmpdir, "sydent.conf"), "w") as cfgfp:
            cfgfp.write(
                CFG_TEMPLATE.format(
                    testsubject_path=testsubject_path,
                    terms_path=terms_path,
                    info_path=info_path,
                    port=port,
                )
            )

        newEnv = os.environ.copy()
        newEnv.update(
            {
                "PYTHONPATH": sydent_path,
            }
        )

        stderr_fp = open(os.path.join(testsubject_path, "sydent.stderr"), "w")

        pybin = os.getenv("SYDENT_PYTHON", "python")

        self.process = Popen(
            args=[pybin, "-m", "sydent.sydent"],
            cwd=self.tmpdir,
            env=newEnv,
            stderr=stderr_fp,
        )
        # XXX: wait for startup in a sensible way
        time.sleep(2)

        self._baseUrl = "http://localhost:%d" % (port,)

    def tearDown(self):
        print("Stopping sydent...")
        self.process.terminate()
        shutil.rmtree(self.tmpdir)

    def get_base_url(self):
        return self._baseUrl
