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

from sydent.db.threepid_associations import GlobalAssociationStore
from sydent.http.auth import authV2
from sydent.http.servlets import get_args, json_response
from sydent.http.servlets.hashdetailsservlet import KNOWN_ALGORITHMS

logger = logging.getLogger(__name__)


async def handle_lookup_v2_post(
    request: web.Request, lookup_pepper: str
) -> web.Response:
    """
    Perform lookups with potentially hashed 3PID details.

    Depending on our response to /hash_details, the client will choose a
    hash algorithm and pepper, hash the 3PIDs it wants to lookup, and
    send them to us, along with the algorithm and pepper it used.

    We first check this algorithm/pepper combo matches what we expect,
    then compare the 3PID details to what we have in the database.

    Params: A JSON object containing the following keys:
            * 'addresses': List of hashed/plaintext (depending on the
                           algorithm) 3PID addresses and mediums.
            * 'algorithm': The algorithm the client has used to process
                           the 3PIDs.
            * 'pepper': The pepper the client has attached to the 3PIDs.

    Returns: Object with key 'mappings', which is a dictionary of results
             where each result is a key/value pair of what the client sent, and
             the matching Matrix User ID that claims to own that 3PID.

             User IDs for which no mapping is found are omitted.
    """
    sydent = request.app["sydent"]
    globalAssociationStore = GlobalAssociationStore(sydent)

    await authV2(sydent, request)

    args = await get_args(request, ("addresses", "algorithm", "pepper"))

    addresses = args["addresses"]
    if not isinstance(addresses, list):
        return json_response(
            {"errcode": "M_INVALID_PARAM", "error": "addresses must be a list"},
            status=400,
        )

    algorithm = str(args["algorithm"])
    if algorithm not in KNOWN_ALGORITHMS:
        return json_response(
            {"errcode": "M_INVALID_PARAM", "error": "algorithm is not supported"},
            status=400,
        )

    # Ensure address count is under the configured limit
    limit = sydent.config.general.address_lookup_limit
    if len(addresses) > limit:
        return json_response(
            {
                "errcode": "M_TOO_LARGE",
                "error": "More than the maximum amount of addresses provided",
            },
            status=400,
        )

    pepper = str(args["pepper"])
    if pepper != lookup_pepper:
        return json_response(
            {
                "errcode": "M_INVALID_PEPPER",
                "error": f"pepper does not match '{lookup_pepper}'",
                "algorithm": algorithm,
                "lookup_pepper": lookup_pepper,
            },
            status=400,
        )

    logger.info("Lookup of %d threepid(s) with algorithm %s", len(addresses), algorithm)
    if algorithm == "none":
        # Lookup without hashing
        medium_address_tuples = []
        for address_and_medium in addresses:
            # Parse medium, address components
            address_medium_split = address_and_medium.split()

            # Forbid addresses that contain a space
            if len(address_medium_split) != 2:
                return json_response(
                    {
                        "errcode": "M_UNKNOWN",
                        "error": f'Invalid "address medium" pair: "{address_and_medium}"',
                    },
                    status=400,
                )

            # Get the mxid for the address/medium combo if known
            address, medium = address_medium_split
            medium_address_tuples.append((medium, address))

        # Lookup the mxids
        medium_address_mxid_tuples = globalAssociationStore.getMxids(
            medium_address_tuples
        )

        # Return a dictionary of lookup_string: mxid values
        return json_response(
            {"mappings": {f"{x[1]} {x[0]}": x[2] for x in medium_address_mxid_tuples}}
        )

    elif algorithm == "sha256":
        # Lookup using SHA256 with URL-safe base64 encoding
        mappings = globalAssociationStore.retrieveMxidsForHashes(addresses)

        return json_response({"mappings": mappings})

    return json_response(
        {"errcode": "M_INVALID_PARAM", "error": "algorithm is not supported"},
        status=400,
    )
