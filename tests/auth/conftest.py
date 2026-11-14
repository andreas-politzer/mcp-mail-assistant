"""Fixtures for the OAuth tests. Helpers and constants live in auth_helpers.py."""

import pytest
from starlette.testclient import TestClient

from auth_helpers import ISSUER, PASSWORD, FakeClock, Flow, add_ping
from outbox_mcp.auth.app import create_authenticated_app
from outbox_mcp.auth.config import AuthConfig
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.auth.store import AuthStore


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def auth_config(tmp_path):
    return AuthConfig(
        base_url=ISSUER,
        password_hash=hash_password(PASSWORD, n=2**4),
        db_path=tmp_path / "auth.db",
    )


@pytest.fixture
def store(auth_config, clock):
    return AuthStore(auth_config.db_path, clock=clock)


@pytest.fixture
def app(auth_config, store):
    return create_authenticated_app(auth_config, store, configure=add_ping, json_response=True)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=ISSUER, follow_redirects=False) as c:
        yield c


@pytest.fixture
def flow(client, store, clock):
    return Flow(client, store, clock)
