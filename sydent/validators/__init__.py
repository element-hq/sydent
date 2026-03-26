# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import attr

# how long a user can wait before validating a session after starting it
THREEPID_SESSION_VALIDATION_TIMEOUT_MS = 24 * 60 * 60 * 1000

# how long we keep sessions for after they've been validated
THREEPID_SESSION_VALID_LIFETIME_MS = 24 * 60 * 60 * 1000


@attr.s(frozen=True, slots=True, auto_attribs=True)
class ValidationSession:
    id: int
    medium: str
    address: str
    client_secret: str
    validated: bool
    mtime: int


@attr.s(frozen=True, slots=True, auto_attribs=True)
class TokenInfo:
    token: str
    send_attempt_number: int


class IncorrectClientSecretException(Exception):
    pass


class SessionExpiredException(Exception):
    pass


class InvalidSessionIdException(Exception):
    pass


class IncorrectSessionTokenException(Exception):
    pass


class SessionNotValidatedException(Exception):
    pass


class DestinationRejectedException(Exception):
    pass


class NextLinkValidationException(Exception):
    """Occurs when the next_link parameter provided by a client to 3PID
    validation endpoints is invalid for some reason
    """
    pass
