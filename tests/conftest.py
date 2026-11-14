import pytest

from outbox_mcp.config import Config
from outbox_mcp.security.recipients import Recipient, RecipientList
from outbox_mcp.storage.database import Database


class FakeSender:
    def __init__(self):
        self.sent = []
        self.error = None

    def send(self, message, envelope_from, envelope_to):
        if self.error:
            raise self.error
        self.sent.append((message, envelope_from, envelope_to))


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def config(tmp_path):
    return Config(
        smtp_host="localhost", smtp_port=2525, smtp_user="", smtp_password="",
        smtp_security="none", from_address="me@example.com", from_name="Me",
        recipients_file=tmp_path / "r.json", db_path=tmp_path / "t.db",
    )


@pytest.fixture
def recipients():
    return RecipientList([
        Recipient("Me", "me@example.com", ("myself",)),
        Recipient("Ferdinand", "ferdinand@example.org", ()),
    ])


@pytest.fixture
def db(config):
    return Database(config.db_path)


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def clock():
    return Clock()
