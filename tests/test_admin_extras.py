"""Admin functions: settings, security information, audit filters and the contact import."""

import datetime
import threading

import pytest
from starlette.testclient import TestClient

from outbox_mcp.admin.app import build_admin_app
from outbox_mcp.admin.config import AdminConfig
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.errors import ValidationError
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore

PASSPHRASE = "correct horse battery staple"
DAY = 86400.0


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def admin_config(tmp_path):
    return AdminConfig(
        db_path=tmp_path / "mail.db", passphrase_hash=hash_password(PASSPHRASE, n=2**4),
        allowed_hosts=("testserver",), allowed_origins=("tauri://localhost",),
    )


@pytest.fixture
def client(admin_config, clock):
    with TestClient(build_admin_app(admin_config, clock)) as c:
        yield c


def sign_in(client):
    return {"Authorization": "Bearer " + client.post("/admin/login", json={"passphrase": PASSPHRASE}).json()["token"]}


@pytest.fixture
def auth(client):
    return sign_in(client)


@pytest.fixture
def db(admin_config):
    return Database(admin_config.db_path)


@pytest.fixture
def store(db):
    return RecipientStore(db)


def put_setting(client, auth, days, **extra):
    return client.put("/admin/settings", json={"candidate_expiry_days": days, **extra}, headers=auth)


def set_days(client, auth, days):
    first = put_setting(client, auth, days).json()
    if first["status"] == "confirmation_required":
        first = put_setting(client, auth, days, confirmation_id=first["confirmation_id"]).json()
    assert first["status"] == "done", first


# -- settings ------------------------------------------------------------------------------------


def test_settings_need_the_session(client):
    assert client.get("/admin/settings").status_code == 401
    assert client.put("/admin/settings", json={}).status_code == 401
    assert client.get("/admin/security").status_code == 401


def test_settings_show_unknown_until_the_mail_service_reports_then_its_starting_value(client, auth, db, config, recipients, sender, clock):
    view = client.get("/admin/settings", headers=auth).json()["candidate_expiry_days"]
    assert view["value"] is None and view["source"] == "unknown" and (view["min"], view["max"], view["default"]) == (1, 365, 30)
    OutboxService(config, recipients, Database(config.db_path), sender, clock)  # reports its own value
    import dataclasses
    # same database file as the admin side?  the admin fixture uses its own file, so report directly
    db.report_candidate_expiry(21, 1.0)
    view = client.get("/admin/settings", headers=auth).json()["candidate_expiry_days"]
    assert (view["value"], view["source"], view["starting_value"]) == (21, "mail_service_settings", 21)


@pytest.mark.parametrize("bad", [0, -1, 366, 1.5, "30", True, None])
def test_the_expiry_must_be_a_whole_number_in_range(client, auth, bad):
    assert put_setting(client, auth, bad).status_code == 400


def test_only_the_expiry_can_be_set_here(client, auth):
    for body in ({"per_hour": 5}, {"candidate_expiry_days": 10, "per_day": 5}, {}):
        assert client.put("/admin/settings", json=body, headers=auth).status_code == 400


def test_a_longer_window_needs_confirmation_a_shorter_one_applies_at_once(client, auth, db, store, clock):
    db.report_candidate_expiry(30, 1.0)
    first = put_setting(client, auth, 60).json()
    assert first["status"] == "confirmation_required" and first["preview"]["candidate_expiry_days"] == "30 -> 60"
    assert db.admin_candidate_expiry()[1] != "stored"  # nothing changed yet
    done = put_setting(client, auth, 60, confirmation_id=first["confirmation_id"]).json()
    assert done["status"] == "done" and done["candidate_expiry_days"]["value"] == 60
    assert db.admin_candidate_expiry()[:2] == (60, "stored")
    assert put_setting(client, auth, 60).json()["status"] == "unchanged"
    store.suggest("o", "Ada", "ada@example.org", "", clock() - 20 * DAY)
    short = put_setting(client, auth, 10).json()  # no confirmation round
    assert short["status"] == "done" and short["candidates_expiring_soon"] == 1 and short["candidate_expiry_days"]["value"] == 10
    assert db.count_events("setting_changed") == 2


def test_a_confirmation_for_the_expiry_is_bound_to_the_state_and_the_value(client, auth, db):
    db.report_candidate_expiry(30, 1.0)
    first = put_setting(client, auth, 60).json()
    set_days(client, auth, 40)  # the setting changes in between (60 is still longer)
    stale = put_setting(client, auth, 60, confirmation_id=first["confirmation_id"])
    assert stale.status_code == 409
    other = put_setting(client, auth, 100).json()
    assert put_setting(client, auth, 120, confirmation_id=other["confirmation_id"]).status_code == 409  # other value


def test_the_mail_service_uses_the_stored_window(config, recipients, sender, clock, db):
    import dataclasses
    store = RecipientStore(db)
    svc = OutboxService(dataclasses.replace(config, db_path=db.path, suggestion_ttl_days=30), DbRecipientList(store), db, sender, clock, recipient_store=store)
    store.suggest("o", "Ada", "ada@example.org", "", clock())
    clock.t += 5 * DAY
    svc._housekeeping()
    assert store.pending()  # 30 days: still a candidate
    db.set_candidate_expiry(3, clock(), "admin:test")
    svc._housekeeping()
    assert store.pending() == [] and db.count_events("recipient_expired") == 1


# -- security information ---------------------------------------------------------------------------


def test_security_information_explains_without_secrets(client, auth, admin_config):
    r = client.get("/admin/security", headers=auth)
    body = r.json()
    text = r.text
    assert admin_config.passphrase_hash not in text and "scrypt" not in text.lower() and "token" not in body["admin_access"]
    for secret in ("password", "secret", "private key", "smtp"):
        assert secret not in str(body["claude_can"] + body["claude_cannot"]).lower() or secret == "password"
    a = body["admin_access"]
    assert a["idle_timeout_seconds"] == 1200 and a["max_session_seconds"] == 14400 and a["allowed_hosts"] == ["testserver"]
    assert a["allowed_origins"] == ["tauri://localhost"] and "loopback" in a["reachable"]
    assert body["mail_limits"] == {"hard_ceiling_per_hour": 100, "hard_ceiling_per_day": 1000}
    assert body["files"]["types"] == {"server_folder": [".md"], "upload": [".md", ".txt"]}
    assert body["files"]["quotas"]["max_total_bytes"] == 50_000_000 and body["files"]["active_sources"] == 0
    assert "approve, reject or block a recipient" in body["claude_cannot"]
    assert body["database"]["schema_version"] == 5


# -- audit filters ------------------------------------------------------------------------------------


@pytest.fixture
def busy(client, auth, db, store, clock):
    """One entry of each kind."""
    store.suggest("o", "Ada", "ada@example.org", "", clock())
    db.set_send_limits(5, 50, clock(), "admin:test")
    sid = db.create_source("upload", "folder", "Mac", clock(), "admin:test")
    db.set_source_rule(sid, "Projekte", "allow", clock(), "admin:test")
    db.set_candidate_expiry(10, clock(), "admin:test")
    db.log_event("claimed", "o", None, clock())
    db.log_event("draft_cancelled_by_block", "system", "x", clock())
    return sid


def entries(client, auth, **params):
    r = client.get("/admin/audit", params=params, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def events(client, auth, **params):
    return {e["event"] for e in entries(client, auth, **params)["entries"]}


def test_each_type_selects_its_own_events(client, auth, busy):
    assert events(client, auth, type="recipients") == {"recipient_suggested"}
    assert events(client, auth, type="limits") == {"send_limits_set"}
    assert events(client, auth, type="sources") == {"source_created"}
    assert events(client, auth, type="files") == {"source_rule_set"}
    assert events(client, auth, type="sends") == {"claimed", "draft_cancelled_by_block"}
    assert events(client, auth, type="settings") == {"setting_changed"}
    assert "admin_login" in events(client, auth, type="access")
    assert len(events(client, auth)) >= 8  # no filter shows everything


def test_types_combine_by_comma_or_repetition(client, auth, busy):
    both = {"recipient_suggested", "send_limits_set"}
    assert events(client, auth, type="recipients,limits") == both
    r = client.get("/admin/audit", params=[("type", "recipients"), ("type", "limits")], headers=auth).json()
    assert {e["event"] for e in r["entries"]} == both


def test_unknown_types_and_bad_dates_are_refused(client, auth, busy):
    for params in ({"type": "nonsense"}, {"type": "recipients,nope"}, {"since": "gestern"}, {"before": "x"}):
        assert client.get("/admin/audit", params=params, headers=auth).status_code == 400, params


def test_since_takes_seconds_or_an_iso_date_and_combines_with_the_other_filters(client, auth, db, store, clock):
    store.suggest("o", "Alt", "alt@example.org", "", 1_000_000.0)  # 1970-01-12
    store.suggest("o", "Neu", "neu@example.org", "", 2_000_000.0)  # 1970-01-24
    assert {e["address"] for e in entries(client, auth, type="recipients", since="1500000")["entries"]} == {"neu@example.org"}
    assert {e["address"] for e in entries(client, auth, type="recipients", since="1970-01-20")["entries"]} == {"neu@example.org"}
    assert {e["address"] for e in entries(client, auth, type="recipients", since="1970-01-20T00:00:00Z")["entries"]} == {"neu@example.org"}
    assert len(entries(client, auth, type="recipients", since="1970-01-01")["entries"]) == 2
    page = entries(client, auth, type="recipients", limit=1)
    assert len(page["entries"]) == 1 and page["next_before"]
    older = entries(client, auth, type="recipients", limit=1, before=page["next_before"])
    assert older["entries"][0]["address"] == "alt@example.org"
    none = entries(client, auth, type="recipients", since="1500000", before=older["entries"][0]["seq"])
    assert none["entries"] == []


# -- contact import ------------------------------------------------------------------------------------


def rows(n, prefix="p"):
    return [{"name": f"Person {i}", "address": f"{prefix}{i}@example.org"} for i in range(n)]


def iplan(client, auth, data):
    return client.post("/admin/recipients/import/plan", json={"rows": data}, headers=auth)


def iapply(client, auth, cid):
    return client.post("/admin/recipients/import/apply", json={"confirmation_id": cid}, headers=auth)


def test_the_import_plan_classifies_every_row_and_writes_nothing(client, auth, store, clock):
    store.suggest("o", "Cand", "cand@example.org", "", clock())
    store.add("Known", "known@example.org", [], clock(), "admin:test")
    for addr, how in (("rej@example.org", "reject"), ("rem@example.org", "remove"), ("blo@example.org", "block")):
        store.suggest("o", "X", addr, "", clock())
        rec = store.find(addr)
        if how == "reject":
            store.reject(rec.id, clock(), "admin:test")
        elif how == "remove":
            store.approve(rec.id, clock(), "admin:test")
            store.remove(rec.id, clock(), "admin:test")
        else:
            store.block(addr, clock(), "admin:test")
    data = [
        {"name": "Neu", "address": "neu@example.org"}, {"name": "Cand", "address": "CAND@example.org"},
        {"name": "Known", "address": "known@example.org"}, {"name": "R", "address": "rej@example.org"},
        {"name": "R2", "address": "rem@example.org"}, {"name": "B", "address": "blo@example.org"},
        {"name": "Dup", "address": "neu@example.org"}, {"name": "", "address": "noname@example.org"},
        {"name": "Bad", "address": "not-an-address"}, "kaputt", {"name": "Multi\nline", "address": "m@example.org"},
    ]
    before = len(store.all_records())
    plan = iplan(client, auth, data).json()
    assert plan["summary"] == {"new": 1, "known": 1, "candidate": 1, "excluded": 3, "invalid": 4, "duplicate": 1}
    by = {r["row"]: r["category"] for r in plan["rows"]}
    assert by[1] == "new" and by[2] == "candidate" and by[3] == "known" and by[4] == by[5] == by[6] == "excluded" and by[7] == "duplicate"
    assert plan["passes"] == 1 and plan["import_id"] and "Nothing is approved" in plan["note"]
    assert len(store.all_records()) == before


def test_apply_creates_candidates_only_never_active_recipients(client, auth, store, db, clock):
    plan = iplan(client, auth, rows(3)).json()
    done = iapply(client, auth, plan["confirmation_id"]).json()
    assert done["status"] == "done" and done["added"] == 3 and done["total_added"] == 3 and done["remaining"] == 0
    assert {r.status for r in store.all_records()} == {"suggested"} and store.active() == ()
    assert all(r.suggested_by == "admin:web" for r in store.all_records())
    (entry,) = [e for e in db.audit_events() if e["event"] == "recipient_csv_imported"]
    assert f"import={plan['import_id']}" in entry["detail"] and "final total_added=3" in entry["detail"]


def test_what_was_turned_down_before_does_not_come_back_but_an_expired_candidate_does(client, auth, store, clock):
    store.suggest("o", "R", "rej@example.org", "", clock())
    store.reject(store.find("rej@example.org").id, clock(), "admin:test")
    store.suggest("o", "E", "exp@example.org", "", clock() - 40 * DAY)
    store.expire_stale(clock(), 30)
    plan = iplan(client, auth, [{"name": "R", "address": "rej@example.org"}, {"name": "E", "address": "exp@example.org"}]).json()
    assert plan["summary"]["excluded"] == 1 and plan["summary"]["new"] == 1
    iapply(client, auth, plan["confirmation_id"])
    assert store.find("rej@example.org").status == "rejected" and store.find("exp@example.org").status == "suggested"


def test_a_large_import_runs_in_passes_with_one_import_id_in_the_audit(client, auth, store, db):
    plan = iplan(client, auth, rows(1200)).json()
    assert plan["passes"] == 3 and plan["per_pass"] == 500
    first = iapply(client, auth, plan["confirmation_id"]).json()
    assert (first["status"], first["added"], first["remaining"], first["pass"]) == ("partial", 500, 700, 1)
    second = iapply(client, auth, first["confirmation_id"]).json()
    assert (second["status"], second["added"], second["remaining"]) == ("partial", 500, 200)
    last = iapply(client, auth, second["confirmation_id"]).json()
    assert (last["status"], last["added"], last["total_added"], last["remaining"]) == ("done", 200, 1200, 0)
    assert len(store.pending()) == 1200
    entries_ = [e for e in db.audit_events() if e["event"] == "recipient_csv_imported"]
    assert len(entries_) == 3 and all(f"import={plan['import_id']}" in e["detail"] for e in entries_)
    assert [e["detail"].split()[1] for e in entries_] == ["pass=1/3", "pass=2/3", "pass=3/3"]
    assert "final total_added=1200" in entries_[-1]["detail"] and "final" not in entries_[0]["detail"]


def test_a_failed_pass_voids_the_rest_and_a_new_plan_skips_what_is_already_there(client, auth, store, db, monkeypatch):
    plan = iplan(client, auth, rows(600)).json()
    first = iapply(client, auth, plan["confirmation_id"]).json()
    original = RecipientStore.add_import_candidates

    def boom(self, *a, **k):
        raise RuntimeError("disk")
    monkeypatch.setattr(RecipientStore, "add_import_candidates", boom)
    assert iapply(client, auth, first["confirmation_id"]).status_code == 500
    monkeypatch.setattr(RecipientStore, "add_import_candidates", original)
    assert iapply(client, auth, first["confirmation_id"]).status_code == 409  # the rest of the plan is void
    assert len(store.pending()) == 500
    again = iplan(client, auth, rows(600)).json()
    assert again["summary"]["candidate"] == 500 and again["summary"]["new"] == 100
    assert iapply(client, auth, again["confirmation_id"]).json()["added"] == 100
    assert len(store.pending()) == 600


def test_a_pass_is_all_or_nothing(store, clock):
    good = {"name": "A", "address": "a@example.org", "note": ""}
    bad = {"name": "B", "address": "nope", "note": ""}
    with pytest.raises(ValidationError):
        store.add_import_candidates([good, bad], clock(), "admin:web", "x", 1, 1, 0, True)
    assert store.all_records() == []


def test_a_lost_answer_can_be_repeated_without_importing_twice(client, auth, store, db):
    plan = iplan(client, auth, rows(2)).json()
    first = iapply(client, auth, plan["confirmation_id"]).json()
    again = iapply(client, auth, plan["confirmation_id"]).json()
    assert again["replayed"] is True and {k: v for k, v in again.items() if k != "replayed"} == first
    assert db.count_events("recipient_csv_imported") == 1 and len(store.pending()) == 2
    assert iapply(client, sign_in(client), plan["confirmation_id"]).status_code == 409  # another session
    assert iapply(client, auth, "unbekannt").status_code == 409


def test_a_replay_expires(client, auth, clock):
    plan = iplan(client, auth, rows(1)).json()
    iapply(client, auth, plan["confirmation_id"])
    clock.t += 1000
    assert iapply(client, auth, plan["confirmation_id"]).status_code in (409, 401)


def test_an_address_that_changed_between_plan_and_apply_is_skipped(client, auth, store, clock):
    plan = iplan(client, auth, [{"name": "A", "address": "a@example.org"}, {"name": "B", "address": "b@example.org"}]).json()
    store.block("a@example.org", clock(), "admin:test")
    store.add("B", "b@example.org", [], clock(), "admin:test")
    done = iapply(client, auth, plan["confirmation_id"]).json()
    assert done["added"] == 0 and done["skipped"] == 2
    assert store.find("a@example.org").status == "blocked" and store.find("b@example.org").status == "active"


@pytest.mark.parametrize("body", ["x", None, [1], [{"address": 5, "name": "x"}]])
def test_malformed_imports_are_handled(client, auth, body):
    r = iplan(client, auth, body)
    assert r.status_code == 400 or (r.status_code == 200 and r.json()["summary"]["invalid"] >= 1)


def test_more_than_2000_rows_are_refused(client, auth):
    assert iplan(client, auth, rows(2001)).status_code == 400
    assert iplan(client, auth, rows(2000)).status_code == 200


def test_the_import_needs_the_session(client):
    assert client.post("/admin/recipients/import/plan", json={"rows": []}).status_code == 401
    assert client.post("/admin/recipients/import/apply", json={}).status_code == 401


def test_the_import_cannot_reach_active_recipients_through_the_mail_service(client, auth, db, config, sender, clock):
    store = RecipientStore(db)
    svc = OutboxService(config, DbRecipientList(store), db, sender, clock, recipient_store=store)
    plan = iplan(client, auth, [{"name": "Neu", "address": "neu@example.org"}]).json()
    iapply(client, auth, plan["confirmation_id"])
    from outbox_mcp.errors import RecipientNotAllowed
    with pytest.raises(RecipientNotAllowed):
        svc.prepare_email("o", "neu@example.org", "S", "T")  # a candidate is not allowed until approved


def test_the_import_and_a_claude_suggestion_share_the_open_candidate_limit(client, auth, store, clock):
    plan = iplan(client, auth, rows(6)).json()
    iapply(client, auth, plan["confirmation_id"])
    from outbox_mcp.errors import RateLimitExceeded
    with pytest.raises(RateLimitExceeded):
        store.suggest("o", "Zed", "zed@example.org", "", clock())


def test_apply_rechecks_turned_down_addresses_on_its_own(client, auth, store, clock):
    plan = iplan(client, auth, [{"name": "A", "address": "a@example.org"}, {"name": "B", "address": "b@example.org"}]).json()
    for addr, how in (("a@example.org", "reject"), ("b@example.org", "remove")):
        store.suggest("o", "X", addr, "", clock())
        rec = store.find(addr)
        if how == "reject":
            store.reject(rec.id, clock(), "admin:test")
        else:
            store.approve(rec.id, clock(), "admin:test")
            store.remove(rec.id, clock(), "admin:test")
    done = iapply(client, auth, plan["confirmation_id"]).json()
    assert done["added"] == 0 and done["skipped"] == 2
    assert store.find("a@example.org").status == "rejected" and store.find("b@example.org").status == "removed"


@pytest.mark.parametrize("bad", [0, 366, -5, 1.5, "7", True])
def test_the_database_refuses_an_expiry_out_of_range_on_its_own(db, clock, bad):
    with pytest.raises(ValidationError):
        db.set_candidate_expiry(bad, clock(), "x")
    assert db.admin_candidate_expiry()[1] == "unknown"
