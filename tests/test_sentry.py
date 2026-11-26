import gc
from typing import Any

import pytest
import sentry_sdk

from tests.utils import make_sydent


def test_sentry_events_carry_the_server_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that Sydent tags the events it reports to Sentry with its server name."""
    events: list[Any] = []

    def drop_event(event: Any, hint: Any) -> None:
        events.append(event)

    init = sentry_sdk.init

    def init_without_sending(*args: Any, **kwargs: Any) -> Any:
        return init(*args, before_send=drop_event, default_integrations=False, **kwargs)

    monkeypatch.setattr(sentry_sdk, "init", init_without_sending)
    try:
        make_sydent(
            {
                "general": {
                    "server.name": "sydent.test",
                    "sentry_dsn": "https://key@sentry.invalid/1",
                }
            }
        )
        sentry_sdk.capture_message("test message")
    finally:
        # Sydent disables the garbage collector when Sentry is enabled.
        gc.enable()
        init()
        # sentry-sdk 2 keeps tags on the isolation scope, and sentry-sdk 0.13 on the
        # hub's scope. Both outlive init().
        if hasattr(sentry_sdk, "get_isolation_scope"):
            scope = sentry_sdk.get_isolation_scope()
        else:
            scope = sentry_sdk.Hub.current.scope
        scope.remove_tag("sydent_server_name")

    assert [event["tags"] for event in events] == [
        {"sydent_server_name": "sydent.test"}
    ]
