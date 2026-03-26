# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
import time
from typing import TYPE_CHECKING

from sydent.db.accounts import AccountStore
from sydent.util.tokenutils import generateAlphanumericTokenOfLength

if TYPE_CHECKING:
    from sydent.sydent import Sydent


logger = logging.getLogger(__name__)


def issueToken(sydent: "Sydent", user_id: str) -> str:
    """
    Creates an account for the given Matrix user ID, then generates, saves and returns
    an access token for that account.

    :param sydent: The Sydent instance to use for storing the token.
    :param user_id: The Matrix user ID to issue a token for.

    :return: The access token for that account.
    """
    accountStore = AccountStore(sydent)
    accountStore.storeAccount(user_id, int(time.time() * 1000), None)

    new_token = generateAlphanumericTokenOfLength(64)
    accountStore.addToken(user_id, new_token)

    return new_token
