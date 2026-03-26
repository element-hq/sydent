# Copyright 2025 New Vector Ltd.
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from abc import ABC, abstractmethod
from configparser import ConfigParser


class BaseConfig(ABC):
    @abstractmethod
    def parse_config(self, cfg: ConfigParser) -> bool:
        """
        Parse the a section of the config

        :param cfg: the configuration to be parsed

        :return: whether or not cfg has been altered. This method CAN
            return True, but it *shouldn't* as this leads to altering the
            config file.
        """
        pass
