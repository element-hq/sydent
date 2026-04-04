import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.utils import make_sydent


@pytest.fixture
def sydent():
    """Create a Sydent instance with an in-memory database."""
    return make_sydent()


@pytest.fixture
async def client(sydent):
    """Create an aiohttp test client for the Sydent client API."""
    app = sydent.clientApiHttpServer.app
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.fixture
async def replication_client(sydent):
    """Create an aiohttp test client for the Sydent replication API."""
    app = sydent.replicationHttpsServer.app
    async with TestClient(TestServer(app)) as client:
        yield client
