# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.
from typing import Optional


class Account:
    def __init__(
        self, user_id: str, creation_ts: int, consent_version: Optional[str]
    ) -> None:
        """
        :param user_id: The Matrix user ID for the account.
        :param creation_ts: The timestamp in milliseconds of the account's creation.
        :param consent_version: The version of the terms of services that the user last
            accepted.
        """
        self.userId = user_id
        self.creationTs = creation_ts
        self.consentVersion = consent_version
