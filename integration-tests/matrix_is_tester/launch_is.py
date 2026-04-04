# Copyright 2019 The Matrix.org Foundation C.I.C.
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

import atexit

from matrix_is_tester.launcher import MatrixIsTestLauncher

launchers = {}


def get_or_launch_is(with_terms=False):
    global launchers

    key = "withTerms" if with_terms else "noTerms"

    if key not in launchers:
        if not launchers:
            atexit.register(destroy_all)

        launchers[key] = MatrixIsTestLauncher(with_terms)
        launchers[key].launch()

    return launchers[key].get_base_url()


def destroy_all():
    global launchers

    for launcher in launchers.values():
        launcher.tearDown()
