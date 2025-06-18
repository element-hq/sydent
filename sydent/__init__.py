# Copyright 2025 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
from os import environ

if environ.get("RUN_DESPITE_UNSUPPORTED") != "Y":
    # Update your remotes folks.
    announcement = """
    Sydent is no longer being developed under the matrix-org organization. See the
    README.rst for more details.

    Please update your git remote to pull from element-hq/sydent:

       git remote set-url origin git@github.com:element-hq/sydent.git
    """
    print(announcement)
    sys.exit(1)
