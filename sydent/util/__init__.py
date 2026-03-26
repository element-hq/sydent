# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import json
import time
from typing import NoReturn


def time_msec() -> int:
    """
    Get the current time in milliseconds.

    :return: The current time in milliseconds.
    """
    return int(time.time() * 1000)


def _reject_invalid_json(val: str) -> NoReturn:
    """Do not allow Infinity, -Infinity, or NaN values in JSON."""
    raise ValueError("Invalid JSON value: '%s'" % val)


# a custom JSON decoder which will reject Python extensions to JSON.
json_decoder = json.JSONDecoder(parse_constant=_reject_invalid_json)
