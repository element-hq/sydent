# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import random
import string

r = random.SystemRandom()


def generateTokenForMedium(medium: str) -> str:
    """
    Generates a token of a different format depending on the medium, a 32 characters
    alphanumeric one if the medium is email, a 6 characters numeric one otherwise.

    :param medium: The medium to generate a token for.

    :return: The generated token.
    """
    if medium == "email":
        return generateAlphanumericTokenOfLength(32)
    else:
        return generateNumericTokenOfLength(6)


def generateNumericTokenOfLength(length: int) -> str:
    """
    Generates a token of the given length with the character set [0-9].

    :param length: The length of the token to generate.

    :return: The generated token.
    """
    return "".join([r.choice(string.digits) for _ in range(length)])


def generateAlphanumericTokenOfLength(length: int) -> str:
    """
    Generates a token of the given length with the character set [a-zA-Z0-9].

    :param length: The length of the token to generate.

    :return: The generated token.
    """
    return "".join(
        [
            r.choice(string.digits + string.ascii_lowercase + string.ascii_uppercase)
            for _ in range(length)
        ]
    )
