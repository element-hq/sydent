# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging

from aiohttp import web

from sydent.db.accounts import AccountStore
from sydent.db.terms import TermsStore
from sydent.http.auth import authV2
from sydent.http.servlets import MatrixRestError, get_args, json_response
from sydent.terms.terms import get_terms

logger = logging.getLogger(__name__)


async def handle_terms_get(request: web.Request) -> web.Response:
    """
    Get the terms that must be agreed to in order to use this service
    Returns: Object describing the terms that require agreement
    """
    sydent = request.app["sydent"]

    terms = get_terms(sydent)

    return json_response(terms.getForClient())


async def handle_terms_post(request: web.Request) -> web.Response:
    """
    Mark a set of terms and conditions as having been agreed to
    """
    sydent = request.app["sydent"]

    account = await authV2(sydent, request, False)

    args = await get_args(request, ("user_accepts",))

    user_accepts = args["user_accepts"]

    terms = get_terms(sydent)
    unknown_urls = list(set(user_accepts) - terms.getUrlSet())
    if len(unknown_urls) > 0:
        raise MatrixRestError(
            400, "M_UNKNOWN", "Unrecognised URLs: {}".format(", ".join(unknown_urls))
        )

    termsStore = TermsStore(sydent)
    termsStore.addAgreedUrls(account.userId, user_accepts)

    all_accepted_urls = termsStore.getAgreedUrls(account.userId)

    if terms.urlListIsSufficient(all_accepted_urls):
        accountStore = AccountStore(sydent)
        accountStore.setConsentVersion(account.userId, terms.getMasterVersion())

    return json_response({})
