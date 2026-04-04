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

import random
import re
import string

import requests
from twisted.python import log

from matrix_is_tester.fakehs import token_for_random_user


class IsApi(object):
    """
    Wrappers around the IS REST API
    """

    def __init__(self, base_url, version, mail_sink):
        """
        Args:
            base_url (str): The base URL of the IS API to use
            version (str): Version of the IS API (eg. 'v1' or 'v2')
                XXX: make these either bytes or unicode and fix up the call sites
                to use the right one.
            mail_sink (MailSink): Mail sink object to use for getting email
                authentication tokens.
        """
        self.headers = None

        self.version = version
        self.base_url = base_url
        if version == "v1":
            self.apiRoot = base_url + "/_matrix/identity/api/v1"
        elif version == "v2":
            self.apiRoot = base_url + "/_matrix/identity/v2"
        else:
            raise Exception("Invalid version: %s" % (version,))

        self.mail_sink = mail_sink

    # Uses the /register API to create an account. This account will
    # be used for all subsequent API calls that requrie auth.
    def make_account(self, hs_addr, openid_token=None):
        if self.version != "v2":
            raise Exception("Only v2 supports authentication")

        if openid_token is None:
            openid_token = token_for_random_user()

        body = self.register(":".join([str(x) for x in hs_addr]), openid_token)
        self.headers = {"Authorization": "Bearer %s" % (body["token"],)}

    def get_token_from_mail(self):
        mail = self.mail_sink.get_mail()

        log.msg("Got email: %r" % (mail,))
        if "data" not in mail:
            raise Exception("Mail has no 'data'")

        data = mail["data"]
        if isinstance(data, bytes):
            data = data.decode("UTF-8")

        matches = re.match(r"<<<(.*)>>>", data)
        if not matches.group(1):
            raise Exception("Failed to match token from mail")

        return matches.group(1)

    def ping(self):
        resp = requests.get(self.apiRoot)
        return resp.json()

    def request_email_code(self, address, client_secret, send_attempt):
        resp = requests.post(
            self.apiRoot + "/validate/email/requestToken",
            json={
                "client_secret": client_secret,
                "email": address,
                "send_attempt": send_attempt,
            },
            headers=self.headers,
        )
        return resp.json()

    def submit_email_token_via_get(self, sid, client_secret, token):
        resp = requests.get(
            self.apiRoot + "/validate/email/submitToken",
            params={"client_secret": client_secret, "sid": sid, "token": token},
            headers=self.headers,
        )
        return resp.content

    def request_and_submit_email_code(self, address):
        client_secret = "".join([random.choice(string.digits) for _ in range(16)])
        req_response = self.request_email_code(address, client_secret, 1)

        token = self.get_token_from_mail()

        sid = req_response["sid"]
        resp = requests.post(
            self.apiRoot + "/validate/email/submitToken",
            json={"client_secret": client_secret, "sid": sid, "token": token},
            headers=self.headers,
        )
        body = resp.json()
        log.msg("submitToken returned %r" % (body,))
        if not body["success"]:
            raise Exception("Submit token failed")
        return {"sid": sid, "client_secret": client_secret}

    def bind_email(self, sid, client_secret, mxid):
        resp = requests.post(
            self.apiRoot + "/3pid/bind",
            json={"client_secret": client_secret, "sid": sid, "mxid": mxid},
            headers=self.headers,
        )
        return resp.json()

    def lookupv1(self, medium, address):
        resp = requests.get(
            self.apiRoot + "/lookup",
            params={"medium": medium, "address": address},
            headers=self.headers,
        )
        return resp.json()

    def bulk_lookup(self, threepids):
        resp = requests.post(
            self.apiRoot + "/bulk_lookup",
            json={"threepids": threepids},
            headers=self.headers,
        )
        return resp.json()

    def get_validated_threepid(self, sid, client_secret):
        resp = requests.get(
            self.apiRoot + "/3pid/getValidated3pid",
            params={"sid": sid, "client_secret": client_secret},
            headers=self.headers,
        )
        return resp.json()

    def store_invite(self, params):
        resp = requests.post(
            self.apiRoot + "/store-invite", json=params, headers=self.headers
        )
        return resp.json()

    def pubkey_is_valid(self, url, pubkey):
        resp = requests.get(url, params={"public_key": pubkey})
        return resp.json()

    def get_terms(self):
        resp = requests.get(self.apiRoot + "/terms")
        return resp.json()

    def agree_to_terms(self, user_accepts):
        resp = requests.post(
            self.apiRoot + "/terms",
            json={"user_accepts": user_accepts},
            headers=self.headers,
        )
        return resp.json()

    def get_versions(self):
        resp = requests.get(self.base_url + "/versions")
        return resp.json()

    def register(self, matrix_server_name, access_token):
        resp = requests.post(
            self.apiRoot + "/account/register",
            json={
                "matrix_server_name": matrix_server_name,
                "access_token": access_token,
            },
        )
        return resp.json()

    def account(self):
        resp = requests.get(self.apiRoot + "/account", headers=self.headers)
        return resp.json()

    def logout(self):
        resp = requests.post(self.apiRoot + "/account/logout", headers=self.headers)
        return resp.json()

    def hash_details(self):
        resp = requests.get(self.apiRoot + "/hash_details", headers=self.headers)
        return resp.json()

    def hashed_lookup(self, addresses, alg, pepper):
        resp = requests.post(
            self.apiRoot + "/lookup",
            json={"addresses": addresses, "algorithm": alg, "pepper": pepper},
            headers=self.headers,
        )
        return resp.json()

    def check_terms_signed(self):
        body = self.hash_details()
        if "algorithms" in body:
            return None
        return body
