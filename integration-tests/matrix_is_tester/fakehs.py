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

import atexit
import base64
import json
import os
import random
import ssl
from multiprocessing import Process

from six.moves import BaseHTTPServer, urllib

shared_fake_hs = None


def token_for_random_user():
    """
    Return an OpenID token as would be obtained from the client/server API.
    The token will represent a random user account.
    """
    num = random.randint(0, 2**32)
    user_id = "@user%d:127.0.0.1:4490" % (num,)
    return "user:%s" % (base64.b64encode(user_id.encode("UTF-8")).decode("UTF-8"),)


def token_for_user(user_id):
    """
    Return an OpenID token as would be obtained from the client/server API.
    The token will represent the user_id given.
    """
    return "user:%s" % (base64.b64encode(user_id.encode("UTF-8")).decode("UTF-8"),)


def get_shared_fake_hs():
    """
    Get the shared fake homeserver object, instantiating it if necessary.
    """
    global shared_fake_hs
    if shared_fake_hs is None:
        shared_fake_hs = FakeHomeserver()
        shared_fake_hs.launch()
        atexit.register(_destroy_shared)
    return shared_fake_hs


def _destroy_shared():
    shared_fake_hs.tearDown()


class _FakeHomeserverRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/_matrix/federation/v1/openid/userinfo"):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            token = params["access_token"][0]

            if token.startswith("user:"):
                userid = base64.b64decode(token.split(":")[1])
            else:
                resp = json.dumps(
                    {
                        "errcode": "M_UNKNOWN_TOKEN",
                        "error": "Not a valid token: try again.",
                    }
                )
                self.send_response(401)
                self.send_header("Content-Length", len(resp))
                self.end_headers()

                self.wfile.write(resp)

            resp = json.dumps({"sub": userid.decode("UTF-8")}).encode("UTF-8")

            self.send_response(200)
            self.send_header("Content-Length", len(resp))
            self.end_headers()

            self.wfile.write(resp)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write("Not found")

    def log_message(self, fmt, *args):
        # don't print to stdout: it screws up the test output (and we don't really care)
        return


def _run_http_server():
    cert_file = os.path.join(os.path.dirname(__file__), "fakehs.pem")

    httpd = BaseHTTPServer.HTTPServer(
        ("127.0.00.1", 4490), _FakeHomeserverRequestHandler
    )
    httpd.socket = ssl.wrap_socket(httpd.socket, certfile=cert_file, server_side=True)
    httpd.serve_forever()


class FakeHomeserver(object):
    """
    A class that spawns an HTTP server that looks like a Matrix Homeserver.
    Currently just implements the federation OpenID endpoint to validate OpenID tokens.
    """

    def launch(self):
        self.process = Process(target=_run_http_server)
        self.process.start()

    def get_addr(self):
        """
        Returns a host, port tuple representing the address on which the fake homeserver
        is listening for requests.
        """
        return ("127.0.0.1", 4490)

    def tearDown(self):
        self.process.terminate()


if __name__ == "__main__":
    fakehs = FakeHomeserver()
    fakehs.launch()
