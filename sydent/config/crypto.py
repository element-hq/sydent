# Copyright 2025 New Vector Ltd.
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from configparser import ConfigParser

import nacl.encoding
import nacl.signing
import signedjson.key
import signedjson.types

from sydent.config._base import BaseConfig


class CryptoConfig(BaseConfig):
    def parse_config(self, cfg: "ConfigParser") -> bool:
        """
        Parse the crypto section of the config
        :param cfg: the configuration to be parsed
        """

        signing_key_str = cfg.get("crypto", "ed25519.signingkey")
        signing_key_parts = signing_key_str.split(" ")

        save_key = False

        # N.B. `signedjson` expects `nacl.signing.SigningKey` instances which
        # have been monkeypatched to include new `alg` and `version` attributes.
        # This is captured by the `signedjson.types.SigningKey` protocol.
        self.signing_key: signedjson.types.SigningKey

        if signing_key_str == "":
            print(
                "INFO: This server does not yet have an ed25519 signing key. "
                "Creating one and saving it in the config file."
            )

            self.signing_key = signedjson.key.generate_signing_key("0")

            save_key = True
        elif len(signing_key_parts) == 1:
            # old format key
            print("INFO: Updating signing key format: brace yourselves")

            self.signing_key = nacl.signing.SigningKey(
                signing_key_str.encode("ascii"), encoder=nacl.encoding.HexEncoder
            )
            self.signing_key.version = "0"
            self.signing_key.alg = signedjson.key.NACL_ED25519

            save_key = True
        else:
            self.signing_key = signedjson.key.decode_signing_key_base64(
                signing_key_parts[0], signing_key_parts[1], signing_key_parts[2]
            )

        if save_key:
            signing_key_str = "%s %s %s" % (
                self.signing_key.alg,
                self.signing_key.version,
                signedjson.key.encode_signing_key_base64(self.signing_key),
            )
            cfg.set("crypto", "ed25519.signingkey", signing_key_str)
            return True
        else:
            return False
