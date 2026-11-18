import logging

import pytest

from sydent.db.valsession import ThreePidValSessionStore
from sydent.validators import IncorrectClientSecretException
from sydent.validators.common import validateSessionWithToken
from tests.utils import make_sydent


def test_incorrect_client_secret_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Test that a wrong client secret is logged with the session ID."""
    sydent = make_sydent()
    session, token_info = ThreePidValSessionStore(sydent).getOrCreateTokenSession(
        "email", "alice@example.com", "right secret"
    )

    with (
        caplog.at_level(logging.INFO, logger="sydent.validators.common"),
        pytest.raises(IncorrectClientSecretException),
    ):
        validateSessionWithToken(sydent, session.id, "wrong secret", token_info.token)

    assert caplog.messages == [f"Incorrect client secret for session {session.id}"]
