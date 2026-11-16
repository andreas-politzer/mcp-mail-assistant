"""The admin side of the scope: command line and API. New recipients are external; own needs a confirmation."""

import io

import pytest
from starlette.testclient import TestClient

from outbox_mcp.admin.app import build_admin_app
from outbox_mcp.admin.config import AdminConfig
from outbox_mcp.admin_cli import main
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import RecipientStore

PASSPHRASE = "correct horse battery staple"


# -- command line -----------------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    return {"OUTBOX_DB_PATH": str(tmp_path / "mail.db")}


def run(args, env, answers=()):
    out = io.StringIO()
    feed = iter(answers)
    code = main(args, env=env, stdout=out, input_func=lambda prompt="": next(feed))
    return code, out.getvalue()


def test_cli_add_is_external_unless_own_is_asked_for(env):
    assert "[external]" in run(["add", "Anna", "anna@example.org"], env)[1]
    assert "[own]" in run(["add", "Berta", "berta@example.org", "--own"], env)[1]
    out = run(["list"], env)[1]
    assert "active, external" in out and "active, own" in out


def test_cli_approve_is_external_unless_own_is_asked_for(env):
    store = RecipientStore(Database(env["OUTBOX_DB_PATH"]))
    store.suggest("o", "Carla", "carla@example.org", "", 1000.0)
    store.suggest("o", "Dora", "dora@example.org", "", 1000.0)
    assert "[external]" in run(["approve", "carla@example.org", "--yes"], env)[1]
    assert "[own]" in run(["approve", "dora@example.org", "--yes", "--own"], env)[1]


def test_cli_scope_command_changes_an_active_recipient_and_refuses_the_rest(env):
    run(["add", "Anna", "anna@example.org"], env)
    assert "[own]" in run(["scope", "anna@example.org", "own"], env)[1]
    assert "[external]" in run(["scope", "anna@example.org", "external"], env)[1]
    code, out = run(["scope", "nobody@example.org", "own"], env)
    assert code != 0 and "No recipient found" in out
    with pytest.raises(SystemExit):
        run(["scope", "anna@example.org", "trusted"], env)


# -- API --------------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path):
    return AdminConfig(
        db_path=tmp_path / "mail.db",
        passphrase_hash=hash_password(PASSPHRASE, n=2**4),
        allowed_hosts=("testserver",),
        allowed_origins=("http://app.test",),
    )


@pytest.fixture
def client(cfg):
    with TestClient(build_admin_app(cfg)) as c:
        yield c


@pytest.fixture
def auth(client):
    token = client.post("/admin/login", json={"passphrase": PASSPHRASE}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def add(client, auth, name="Anna", address="anna@example.org", **extra):
    return client.post("/admin/recipients", json={"name": name, "address": address, **extra}, headers=auth)


def set_scope(client, auth, rid, scope, **extra):
    return client.post(f"/admin/recipients/{rid}/scope", json={"scope": scope, **extra}, headers=auth)


def test_a_recipient_added_through_the_api_is_always_external(client, auth):
    r = add(client, auth)
    assert r.status_code == 201 and r.json()["recipient"]["scope"] == "external"
    # a client that tries to slip 'own' in gets nothing of it
    r = add(client, auth, name="Berta", address="berta@example.org", scope="own")
    assert r.json()["recipient"]["scope"] == "external"
    listed = client.get("/admin/recipients", headers=auth).json()["recipients"]
    assert [x["scope"] for x in listed] == ["external", "external"]


def test_an_approved_suggestion_is_external_whatever_the_request_says(client, auth, cfg):
    RecipientStore(Database(cfg.db_path)).suggest("o", "Carla", "carla@example.org", "", 1000.0)
    rid = client.get("/admin/recipients?status=suggested", headers=auth).json()["recipients"][0]["id"]
    r = client.post(f"/admin/recipients/{rid}/approve", json={"scope": "own"}, headers=auth)
    assert r.status_code == 200 and r.json()["recipient"]["scope"] == "external"


def test_making_an_address_own_needs_a_one_time_confirmation(client, auth):
    rid = add(client, auth).json()["recipient"]["id"]
    first = set_scope(client, auth, rid, "own").json()
    assert first["status"] == "confirmation_required" and "outside the chat" in first["preview"]["effect"]
    assert client.get("/admin/recipients", headers=auth).json()["recipients"][0]["scope"] == "external"
    done = set_scope(client, auth, rid, "own", confirmation_id=first["confirmation_id"]).json()
    assert done["status"] == "done" and done["recipient"]["scope"] == "own"
    again = set_scope(client, auth, rid, "own", confirmation_id=first["confirmation_id"])
    assert again.status_code == 200  # already own: nothing to confirm, nothing changes


def test_a_confirmation_cannot_be_used_twice_or_for_another_recipient(client, auth):
    a = add(client, auth).json()["recipient"]["id"]
    b = add(client, auth, name="Berta", address="berta@example.org").json()["recipient"]["id"]
    first = set_scope(client, auth, a, "own").json()
    wrong = set_scope(client, auth, b, "own", confirmation_id=first["confirmation_id"])
    assert wrong.status_code == 409
    # the first try, even a wrong one, uses the confirmation up
    assert set_scope(client, auth, a, "own", confirmation_id=first["confirmation_id"]).status_code == 409
    second = set_scope(client, auth, a, "own").json()
    assert set_scope(client, auth, a, "own", confirmation_id=second["confirmation_id"]).json()["status"] == "done"
    set_scope(client, auth, a, "external")
    replay = set_scope(client, auth, a, "own", confirmation_id=second["confirmation_id"])
    assert replay.status_code == 409  # used up
    assert client.get("/admin/recipients", headers=auth).json()["recipients"][0]["scope"] == "external"


def test_a_confirmation_does_not_survive_a_change_of_the_recipient(client, auth):
    rid = add(client, auth).json()["recipient"]["id"]
    first = set_scope(client, auth, rid, "own").json()
    client.post(f"/admin/recipients/{rid}/remove", json={}, headers=auth)
    add(client, auth)  # active again, a new life of the record
    stale = set_scope(client, auth, rid, "own", confirmation_id=first["confirmation_id"])
    assert stale.status_code == 409


def test_making_an_address_external_needs_no_confirmation(client, auth):
    rid = add(client, auth).json()["recipient"]["id"]
    done = set_scope(client, auth, rid, "own")
    set_scope(client, auth, rid, "own", confirmation_id=done.json()["confirmation_id"])
    r = set_scope(client, auth, rid, "external")
    assert r.json()["status"] == "done" and r.json()["recipient"]["scope"] == "external"


def test_scope_input_is_checked_and_needs_sign_in(client, auth):
    rid = add(client, auth).json()["recipient"]["id"]
    for bad in ("trusted", "", None, 1, ["own"]):
        assert set_scope(client, auth, rid, bad).status_code == 400
    assert client.post(f"/admin/recipients/{rid}/scope", json={"scope": "external"}).status_code == 401
    client.post(f"/admin/recipients/{rid}/remove", json={}, headers=auth)
    assert set_scope(client, auth, rid, "own").status_code == 400  # only active recipients have a scope
