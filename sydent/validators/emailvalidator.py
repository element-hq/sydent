# Copyright 2025 New Vector Ltd.
# Copyright 2014 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import logging
import urllib
from typing import TYPE_CHECKING, Dict, Optional

from sydent.db.valsession import ThreePidValSessionStore
from sydent.util import time_msec
from sydent.util.emailutils import sendEmail
from sydent.validators import common

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)


class EmailValidator:
    def __init__(self, sydent: "Sydent") -> None:
        self.sydent = sydent

    def requestToken(
        self,
        emailAddress: str,
        clientSecret: str,
        sendAttempt: int,
        nextLink: Optional[str],
        ipaddress: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> int:
        """
        Creates or retrieves a validation session and sends an email to the corresponding
        email address with a token to use to verify the association.

        :param emailAddress: The email address to send the email to.
        :param clientSecret: The client secret to use.
        :param sendAttempt: The current send attempt.
        :param nextLink: The link to redirect the user to once they have completed the
            validation.
        :param ipaddress: The requester's IP address.
        :param brand: A hint at a brand from the request.

        :return: The ID of the session created (or of the existing one if any)
        """
        valSessionStore = ThreePidValSessionStore(self.sydent)

        valSession, token_info = valSessionStore.getOrCreateTokenSession(
            medium="email", address=emailAddress, clientSecret=clientSecret
        )

        valSessionStore.setMtime(valSession.id, time_msec())

        # self.sydent.config.email.template is deprecated
        if self.sydent.config.email.template is None:
            templateFile = self.sydent.get_branded_template(
                brand,
                "verification_template.eml",
            )
        else:
            templateFile = self.sydent.config.email.template

        if token_info.send_attempt_number >= sendAttempt:
            logger.info(
                "Not mailing code because current send attempt (%d) is not less than given send attempt (%s)",
                sendAttempt,
                token_info.send_attempt_number,
            )
            return valSession.id

        ipstring = ipaddress if ipaddress else "an unknown location"

        substitutions = {
            "ipaddress": ipstring,
            "link": self.makeValidateLink(
                valSession.id, token_info.token, clientSecret, nextLink
            ),
            "token": token_info.token,
        }
        logger.info(
            "Attempting to mail code %s (nextLink: %s) to %s",
            token_info.token,
            nextLink,
            emailAddress,
        )
        sendEmail(self.sydent, templateFile, emailAddress, substitutions)

        valSessionStore.setSendAttemptNumber(valSession.id, sendAttempt)

        return valSession.id

    def makeValidateLink(
        self,
        session_id: int,
        token: str,
        clientSecret: str,
        nextLink: Optional[str],
    ) -> str:
        """
        Creates a validation link that can be sent via email to the user.

        :param session_id: The current validation session's ID.
        :param token: The token to make a link for.
        :param clientSecret: The client secret to include in the link.
        :param nextLink: The link to redirect the user to once they have completed the
            validation.

        :return: The validation link.
        """
        base = self.sydent.config.http.server_http_url_base
        link = "%s/_matrix/identity/api/v1/validate/email/submitToken?token=%s&client_secret=%s&sid=%d" % (
            base,
            urllib.parse.quote(token),
            urllib.parse.quote(clientSecret),
            session_id,
        )
        if nextLink:
            # manipulate the nextLink to add the sid, because
            # the caller won't get it until we send a response,
            # by which time we've sent the mail.
            if "?" in nextLink:
                nextLink += "&"
            else:
                nextLink += "?"
            nextLink += "sid=" + urllib.parse.quote(str(session_id))

            link += "&nextLink=%s" % (urllib.parse.quote(nextLink))
        return link

    def validateSessionWithToken(self, sid: int, clientSecret: str, token: str, next_link=None) -> Dict[str, bool]:
        """
        Validates the session with the given ID.

        :param sid: The ID of the session to validate.
        :param clientSecret: The client secret to validate.
        :param token: The token to validate.
        :param next_link: The link to redirect the client to after validation, if provided
        :type next_link: unicode or None

        :return: A dict with a "success" key which is True if the session
            was successfully validated, False otherwise.
        """
        return common.validateSessionWithToken(
            self.sydent, sid, clientSecret, token, next_link
        )
