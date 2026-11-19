# Copyright 2025 New Vector Ltd.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
from __future__ import annotations

from typing import Any, Generator

import pytest

from tests.blackbox.environment import SYDENTS, Env
from tests.blackbox.fakes import TestCA


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: Any
) -> Generator[None, Any, None]:
    outcome = yield
    report = outcome.get_result()
    if not report.failed:
        return
    nodes = [item, item.getparent(pytest.Module)]
    for node in nodes:
        if node is None:
            continue
        for s in node.stash.get(SYDENTS, []):
            report.sections.append(
                (f"Sydent {s.server_name} ({s.directory})", s.log_tail())
            )


@pytest.fixture(scope="session")
def ca(tmp_path_factory: pytest.TempPathFactory) -> TestCA:
    return TestCA(str(tmp_path_factory.mktemp("ca")))


def _env(
    request: pytest.FixtureRequest, factory: pytest.TempPathFactory, ca: TestCA
) -> Generator[Env, None, None]:
    directory = str(factory.mktemp("bb", numbered=True))
    env = Env(directory, ca, request.node)
    try:
        yield env
    finally:
        env.close()


@pytest.fixture
def env(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory, ca: TestCA
) -> Generator[Env, None, None]:
    """Fakes and Sydents for one test, stopped when it ends."""
    yield from _env(request, tmp_path_factory, ca)


@pytest.fixture(scope="module")
def menv(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory, ca: TestCA
) -> Generator[Env, None, None]:
    """Like `env`, shared by every test in a module."""
    yield from _env(request, tmp_path_factory, ca)
