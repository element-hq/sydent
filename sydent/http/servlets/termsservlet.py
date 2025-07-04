# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
from typing import TYPE_CHECKING

from twisted.web.server import Request

from sydent.db.accounts import AccountStore
from sydent.db.terms import TermsStore
from sydent.http.auth import authV2
from sydent.http.servlets import (
    MatrixRestError,
    SydentResource,
    get_args,
    jsonwrap,
    send_cors,
)
from sydent.terms.terms import get_terms
from sydent.types import JsonDict

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)


class TermsServlet(SydentResource):
    isLeaf = True

    def __init__(self, syd: "Sydent") -> None:
        super().__init__()
        self.sydent = syd

    @jsonwrap
    def render_GET(self, request: Request) -> JsonDict:
        """
        Get the terms that must be agreed to in order to use this service
        Returns: Object describing the terms that require agreement
        """
        send_cors(request)

        terms = get_terms(self.sydent)

        return terms.getForClient()

    @jsonwrap
    def render_POST(self, request: Request) -> JsonDict:
        """
        Mark a set of terms and conditions as having been agreed to
        """
        send_cors(request)

        account = authV2(self.sydent, request, False)

        args = get_args(request, ("user_accepts",))

        user_accepts = args["user_accepts"]

        terms = get_terms(self.sydent)
        unknown_urls = list(set(user_accepts) - terms.getUrlSet())
        if len(unknown_urls) > 0:
            raise MatrixRestError(
                400, "M_UNKNOWN", "Unrecognised URLs: %s" % (", ".join(unknown_urls),)
            )

        termsStore = TermsStore(self.sydent)
        termsStore.addAgreedUrls(account.userId, user_accepts)

        all_accepted_urls = termsStore.getAgreedUrls(account.userId)

        if terms.urlListIsSufficient(all_accepted_urls):
            accountStore = AccountStore(self.sydent)
            accountStore.setConsentVersion(account.userId, terms.getMasterVersion())

        return {}

    def render_OPTIONS(self, request: Request) -> bytes:
        send_cors(request)
        return b""
