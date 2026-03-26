# Copyright 2025 New Vector Ltd.
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from configparser import ConfigParser

from sydent.config._base import BaseConfig


class DatabaseConfig(BaseConfig):
    def parse_config(self, cfg: "ConfigParser") -> bool:
        """
        Parse the database section of the config

        :param cfg: the configuration to be parsed
        """
        self.database_path = cfg.get("db", "db.file")

        return False
