# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import hashlib

import unpaddedbase64


def sha256_and_url_safe_base64(input_text: str) -> str:
    """SHA256 hash an input string, encode the digest as url-safe base64, and
    return

    :param input_text: string to hash

    :returns a sha256 hashed and url-safe base64 encoded digest
    """
    digest = hashlib.sha256(input_text.encode()).digest()
    return unpaddedbase64.encode_base64(digest, urlsafe=True)
