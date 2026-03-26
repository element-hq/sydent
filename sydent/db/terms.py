# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from sydent.sydent import Sydent


class TermsStore:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent

    def getAgreedUrls(self, user_id: str) -> List[str]:
        """
        Retrieves the URLs of the terms the given user has agreed to.

        :param user_id: Matrix user ID to fetch the URLs for.

        :return: A list of the URLs of the terms accepted by the user.
        """
        cur = self.sydent.db.cursor()
        res = cur.execute(
            "select url from accepted_terms_urls " "where user_id = ?",
            (user_id,),
        )

        urls = []
        for (url,) in res:
            # Ensure we're dealing with unicode.
            if url and isinstance(url, bytes):
                url = url.decode("UTF-8")

            urls.append(url)

        return urls

    def addAgreedUrls(self, user_id: str, urls: List[str]) -> None:
        """
        Saves that the given user has accepted the terms at the given URLs.

        :param user_id: The Matrix user ID that has accepted the terms.
        :param urls: The list of URLs.
        """
        cur = self.sydent.db.cursor()
        cur.executemany(
            "insert or ignore into accepted_terms_urls (user_id, url) values (?, ?)",
            ((user_id, u) for u in urls),
        )
        self.sydent.db.commit()
