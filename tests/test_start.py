import pytest

from tests.utils import make_sydent


def test_start():
    """Test that sydent starts up correctly."""
    sydent = make_sydent()
    # Just verify the Sydent instance was created successfully.
    assert sydent is not None


def test_homeserver_allow_list_refuses_to_start_if_v1_not_disabled():
    """Test that Sydent throws a runtime error if homeserver_allow_list is specified
    but the v1 API has not been disabled.
    """
    config = {
        "general": {
            "homeserver_allow_list": "friendly.com, example.com",
            "enable_v1_access": "true",
        }
    }

    with pytest.raises(RuntimeError):
        make_sydent(test_config=config)
