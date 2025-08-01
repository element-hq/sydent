# Copyright 2025 New Vector Ltd.
# Copyright 2014, 2015 OpenMarket Ltd
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import email.utils
import logging
import random
import smtplib
import ssl
import string
import urllib
from html import escape
from typing import TYPE_CHECKING, Dict

import twisted.python.log
from prometheus_client import Counter

from sydent.util import time_msec
from sydent.util.tokenutils import generateAlphanumericTokenOfLength

if TYPE_CHECKING:
    from sydent.sydent import Sydent

logger = logging.getLogger(__name__)

email_counter = Counter("sydent_emails_sent", "Number of emails we attempted to send")


def sendEmail(
    sydent: "Sydent",
    templateFile: str,
    mailTo: str,
    substitutions: Dict[str, str],
    log_send_errors: bool = True,
) -> None:
    """
    Sends an email with the given parameters.

    :param sydent: The Sydent instance to use when building the configuration to send the
        email with.
    :param templateFile: The filename of the template to use when building the body of the
        email.
    :param mailTo: The email address to send the email to.
    :param substitutions: The substitutions to use with the template.
    :param log_send_errors: Whether to log errors happening when sending an email.
    """
    mailFrom = sydent.config.email.sender
    myHostname = sydent.config.email.host_name

    midRandom = "".join([random.choice(string.ascii_letters) for _ in range(16)])
    messageid = "<%d%s@%s>" % (time_msec(), midRandom, myHostname)

    substitutions.update(
        {
            "messageid": messageid,
            "date": email.utils.formatdate(localtime=False),
            "to": mailTo,
            "from": mailFrom,
        }
    )

    # use jinja for rendering if jinja templates are present
    if templateFile.endswith(".j2"):
        # We add randomize the multipart boundary to stop user input from
        # conflicting with it.
        substitutions["multipart_boundary"] = generateAlphanumericTokenOfLength(32)
        template = sydent.config.general.template_environment.get_template(templateFile)
        mailString = template.render(substitutions)
    else:
        allSubstitutions = {}
        for k, v in substitutions.items():
            allSubstitutions[k] = v
            allSubstitutions[k + "_forhtml"] = escape(v)
            allSubstitutions[k + "_forurl"] = urllib.parse.quote(v)
        allSubstitutions["multipart_boundary"] = generateAlphanumericTokenOfLength(32)
        with open(templateFile) as template_file:
            mailString = template_file.read() % allSubstitutions

    try:
        check_valid_email_address(mailTo, allow_description=False)
    except EmailAddressException:
        logger.warning("Invalid email address %s", mailTo)
        raise

    mailServer = sydent.config.email.smtp_server
    mailPort = int(sydent.config.email.smtp_port)
    mailUsername = sydent.config.email.smtp_username
    mailPassword = sydent.config.email.smtp_password
    mailTLSMode = sydent.config.email.tls_mode

    logger.info(
        "Sending mail to %s with mail server: %s"
        % (
            mailTo,
            mailServer,
        )
    )
    try:
        smtp: smtplib.SMTP
        # Explicitly create a context, to ensure we verify the server's certificate
        # and hostname.
        ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        if mailTLSMode == "SSL" or mailTLSMode == "TLS":
            smtp = smtplib.SMTP_SSL(mailServer, mailPort, myHostname, context=ctx)
        elif mailTLSMode == "STARTTLS":
            smtp = smtplib.SMTP(mailServer, mailPort, myHostname)
            smtp.starttls(context=ctx)
        else:
            smtp = smtplib.SMTP(mailServer, mailPort, myHostname)
        if mailUsername != "":
            smtp.login(mailUsername, mailPassword)

        email_counter.inc()

        # We're using the parsing above to do basic validation, but instead of
        # failing it may munge the address it returns. So we should *not* use
        # that parsed address, as it may not match any validation done
        # elsewhere.
        # Replace the line endings (typically "\n") with "\r\n" (CRLF) as required by email.
        # This avoids "550 5.6.11 SMTPSEND.BareLinefeedsAreIllegal" errors when
        # sending to strict mail servers.
        mailStringCRLF = "\r\n".join(mailString.splitlines())
        smtp.sendmail(mailFrom, mailTo, mailStringCRLF.encode("utf-8"))
        smtp.quit()
    except Exception as origException:
        if log_send_errors:
            twisted.python.log.err()
        raise EmailSendException() from origException


def check_valid_email_address(address: str, allow_description: bool) -> None:
    """Check the given string is a valid email address.

    Email addresses are complicated (see RFCs 5321, 5322 and 6531; plus
    https://www.netmeister.org/blog/email.html). This isn't a comprehensive
    validation; we defer to Python's stdlib.

    :raises EmailAddressException: if not.
    """
    parsed_address = email.utils.parseaddr(address)[1]
    if parsed_address == "":
        raise EmailAddressException(f"Couldn't parse email address {address}.")
    if not allow_description and address != parsed_address:
        raise EmailAddressException(
            f"Parsing address ({address} yielded a different address"
            f"({parsed_address})"
        )


class EmailAddressException(Exception):
    pass


class EmailSendException(Exception):
    pass
