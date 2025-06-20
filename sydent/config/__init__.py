# Copyright 2025 New Vector Ltd.
# Copyright 2019-2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import copy
import logging.handlers
import os
from configparser import DEFAULTSECT, ConfigParser
from typing import Dict

from sydent.config.crypto import CryptoConfig
from sydent.config.database import DatabaseConfig
from sydent.config.email import EmailConfig
from sydent.config.general import GeneralConfig
from sydent.config.http import HTTPConfig
from sydent.config.sms import SMSConfig

logger = logging.getLogger(__name__)

CONFIG_DEFAULTS = {
    "general": {
        "server.name": os.environ.get("SYDENT_SERVER_NAME", ""),
        "log.path": "",
        "log.level": "INFO",
        "pidfile.path": os.environ.get("SYDENT_PID_FILE", "sydent.pid"),
        "terms.path": "",
        "address_lookup_limit": "10000",  # Maximum amount of addresses in a single /lookup request
        # The root path to use for load templates. This should contain branded
        # directories. Each directory should contain the following templates:
        #
        # * invite_template.eml
        # * verification_template.eml
        # * verify_response_template.html
        "templates.path": "res",
        # The brand directory to use if no brand hint (or an invalid brand hint)
        # is provided by the request.
        "brand.default": "matrix-org",
        # The following can be added to your local config file to enable prometheus
        # support.
        # 'prometheus_port': '8080',  # The port to serve metrics on
        # 'prometheus_addr': '',  # The address to bind to. Empty string means bind to all.
        # The following can be added to your local config file to enable sentry support.
        # 'sentry_dsn': 'https://...'  # The DSN has configured in the sentry instance project.
        # Whether clients and homeservers can register an association using v1 endpoints. This
        # option is now deprecated and will be superceded by the option `enable_v1_access`
        "enable_v1_associations": "true",
        "delete_tokens_on_bind": "true",
        # Prevent outgoing requests from being sent to the following blacklisted
        # IP address CIDR ranges. If this option is not specified or empty then
        # it defaults to private IP address ranges.
        #
        # The blacklist applies to all outbound requests except replication
        # requests.
        #
        # (0.0.0.0 and :: are always blacklisted, whether or not they are
        # explicitly listed here, since they correspond to unroutable
        # addresses.)
        "ip.blacklist": "",
        # List of IP address CIDR ranges that should be allowed for outbound
        # requests. This is useful for specifying exceptions to wide-ranging
        # blacklisted target IP ranges.
        #
        # This whitelist overrides `ip.blacklist` and defaults to an empty
        # list.
        "ip.whitelist": "",
        # A list of homeservers that are allowed to register with this identity server. Defaults to
        # allowing all homeservers. If a list is specified, the config option `enable_v1_access` must be
        # set to 'false'.
        "homeserver_allow_list": "",
        # If set to 'false', entirely disable access via the V1 api.
        "enable_v1_access": "true",
    },
    "db": {
        "db.file": os.environ.get("SYDENT_DB_PATH", "sydent.db"),
    },
    "http": {
        "clientapi.http.bind_address": "::",
        "clientapi.http.port": "8090",
        "internalapi.http.bind_address": "::1",
        "internalapi.http.port": "",
        "replication.https.certfile": "",
        "replication.https.cacert": "",  # This should only be used for testing
        "replication.https.bind_address": "::",
        "replication.https.port": "4434",
        "obey_x_forwarded_for": "False",
        "federation.verifycerts": "True",
        # verify_response_template is deprecated, but still used if defined. Define
        # templates.path and brand.default under general instead.
        #
        # 'verify_response_template': 'res/verify_response_page_template',
        "client_http_base": "",
    },
    "email": {
        # email.template and email.invite_template are deprecated, but still used
        # if defined. Define templates.path and brand.default under general instead.
        #
        # 'email.template': 'res/verification_template.eml',
        # 'email.invite_template': 'res/invite_template.eml',
        "email.from": "Sydent Validation <noreply@{hostname}>",
        "email.subject": "Your Validation Token",
        "email.invite.subject": "%(sender_display_name)s has invited you to chat",
        "email.invite.subject_space": "%(sender_display_name)s has invited you to a space",
        "email.smtphost": "localhost",
        "email.smtpport": "25",
        "email.smtpusername": "",
        "email.smtppassword": "",
        "email.hostname": "",
        "email.tlsmode": "0",
        # The web client location which will be used if it is not provided by
        # the homeserver.
        #
        # This should be the scheme and hostname only, see res/invite_template.eml
        # for the full URL that gets generated.
        "email.default_web_client_location": "https://app.element.io",
        # When a user is invited to a room via their email address, that invite is
        # displayed in the room list using an obfuscated version of the user's email
        # address. These config options determine how much of the email address to
        # obfuscate. Note that the '@' sign is always included.
        #
        # If the string is longer than a configured limit below, it is truncated to that limit
        # with '...' added. Otherwise:
        #
        # * If the string is longer than 5 characters, it is truncated to 3 characters + '...'
        # * If the string is longer than 1 character, it is truncated to 1 character + '...'
        # * If the string is 1 character long, it is converted to '...'
        #
        # This ensures that a full email address is never shown, even if it is extremely
        # short.
        #
        # The number of characters from the beginning to reveal of the email's username
        # portion (left of the '@' sign)
        "email.third_party_invite_username_obfuscate_characters": "3",
        # The number of characters from the beginning to reveal of the email's domain
        # portion (right of the '@' sign)
        "email.third_party_invite_domain_obfuscate_characters": "3",
    },
    "sms": {
        "bodyTemplate": "Your code is {token}",
        "username": "",
        "password": "",
    },
    "crypto": {
        "ed25519.signingkey": "",
    },
}


class SydentConfig:
    """This is the class in charge of handling Sydent's configuration.
    Handling of each individual section is delegated to other classes
    stored in a `config_sections` list.

    To use this class, create a new object and then call one of
    `parse_config_file` or `parse_config_dict` before creating the
    Sydent object that uses it.
    """

    def __init__(self) -> None:
        self.general = GeneralConfig()
        self.database = DatabaseConfig()
        self.crypto = CryptoConfig()
        self.sms = SMSConfig()
        self.email = EmailConfig()
        self.http = HTTPConfig()

        self.config_sections = [
            self.general,
            self.database,
            self.crypto,
            self.sms,
            self.email,
            self.http,
        ]

    def _parse_config(self, cfg: ConfigParser) -> bool:
        """
        Run the parse_config method on each of the objects in
        self.config_sections

        :param cfg: the configuration to be parsed

        :return: whether or not cfg has been altered. This method CAN
            return True, but it *shouldn't* as this leads to altering the
            config file.
        """
        needs_saving = False
        for section in self.config_sections:
            if section.parse_config(cfg):
                needs_saving = True

        return needs_saving

    def parse_from_config_parser(self, cfg: ConfigParser) -> bool:
        """
        Parse the configuration from a ConfigParser object

        :param cfg: the configuration to be parsed

        :return: whether or not cfg has been altered. This method CAN
            return True, but it *shouldn't* as this leads to altering the
            config file.
        """
        return self._parse_config(cfg)

    def parse_config_file(self, config_file: str) -> None:
        """
        Parse the given config from a filepath, populating missing items and
        sections.

        :param config_file: the file to be parsed
        """
        # If the config file already exists, place all config options in
        # the DEFAULT section, to avoid overriding any of the user's
        # configured values in the sections other than DEFAULT.
        #
        # We don't always do this as earlier Sydent versions required
        # users to put their settings in the DEFAULT section.
        #
        # We want to avoid overwriting those.
        use_defaults = os.path.exists(config_file)

        cfg = ConfigParser()
        for sect, entries in CONFIG_DEFAULTS.items():
            cfg.add_section(sect)
            for k, v in entries.items():
                cfg.set(DEFAULTSECT if use_defaults else sect, k, v)

        cfg.read(config_file)

        # TODO: Don't alter config file when starting Sydent so that
        #       it can be set to read-only
        needs_saving = self.parse_from_config_parser(cfg)

        if needs_saving:
            fp = open(config_file, "w")
            cfg.write(fp)
            fp.close()

    def parse_config_dict(self, config_dict: Dict[str, Dict[str, str]]) -> None:
        """
        Parse the given config from a dictionary, populating missing items and sections

        :param config_dict: the configuration dictionary to be parsed
        """
        # Build a config dictionary from the defaults merged with the given dictionary
        config = copy.deepcopy(CONFIG_DEFAULTS)
        for section, section_dict in config_dict.items():
            if section not in config:
                config[section] = {}
            for option in section_dict.keys():
                config[section][option] = config_dict[section][option]

        # Build a ConfigParser from the merged dictionary
        cfg = ConfigParser()
        for section, section_dict in config.items():
            cfg.add_section(section)
            for option, value in section_dict.items():
                cfg.set(section, option, value)

        self.parse_from_config_parser(cfg)
