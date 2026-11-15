"""Send limits stored in the database: hard ceilings, how sends are counted, parallel safety, admin endpoints."""

import sqlite3
import threading

import pytest
from starlette.testclient import TestClient

from outbox_mcp.admin.app import build_admin_app
from outbox_mcp.admin.config import AdminConfig
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.errors import DraftNotSendable, RateLimitExceeded, ValidationError
from outbox_mcp.security.rate_limiter import (
    DAY, HARD_MAX_SENDS_PER_DAY, HARD_MAX_SENDS_PER_HOUR, HOUR, RateLimits,
)
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore

BASE = RateLimits(max_prepares_per_hour=10_000, max_sends_per_hour=3, max_sends_per_day=10)
NOW = 1_000_000.0
PASSPHRASE = "correct horse battery staple"


def draft(db, n, now=NOW):
    return db.create_draft("o", "Me", "me@example.com", f"S{n}", "body", now, 100 * DAY, BASE).id


def claim(db, n, now=NOW, base=BASE):
    return db.claim_for_sending(draft(db, n, now), "o", now, base)


def stored(db):
    return db.send_limits(BASE)


# -- ceilings and defaults ---------------------------------------------------------------

def test_hard_ceilings_are_the_agreed_values():
    assert (HARD_MAX_SENDS_PER_HOUR, HARD_MAX_SENDS_PER_DAY) == (100, 1000)


def test_without_stored_values_the_starting_values_apply(db):
    assert (stored(db).max_sends_per_hour, stored(db).max_sends_per_day) == (3, 10)
    assert db.limits_are_stored() is False


def test_starting_values_above_the_ceilings_are_cut_down(db):
    huge = RateLimits(max_prepares_per_hour=1, max_sends_per_hour=5000, max_sends_per_day=999_999)
    eff = db.send_limits(huge)
    assert (eff.max_sends_per_hour, eff.max_sends_per_day) == (100, 1000)


def test_a_value_written_around_the_api_is_still_cut_down(db):
    conn = sqlite3.connect(db.path)
    conn.execute("INSERT INTO settings(key,value,changed_at,changed_by) VALUES ('send_limit_per_hour','5000',1,'x')")
    conn.execute("INSERT INTO settings(key,value,changed_at,changed_by) VALUES ('send_limit_per_day','-7',1,'x')")
    conn.commit()
    conn.close()
    eff = stored(db)
    assert (eff.max_sends_per_hour, eff.max_sends_per_day) == (100, 0)


def test_an_unreadable_stored_value_falls_back_to_the_starting_value(db):
    conn = sqlite3.connect(db.path)
    conn.execute("INSERT INTO settings(key,value,changed_at,changed_by) VALUES ('send_limit_per_hour','lots',1,'x')")
    conn.commit()
    conn.close()
    assert stored(db).max_sends_per_hour == 3


def test_the_ceiling_holds_at_send_time_even_for_huge_stored_values(db):
    conn = sqlite3.connect(db.path)
    for key in ("send_limit_per_hour", "send_limit_per_day"):
        conn.execute("INSERT INTO settings(key,value,changed_at,changed_by) VALUES (?,'100000',1,'x')", (key,))
    conn.commit()
    conn.close()
    results = []
    for n in range(105):
        try:
            claim(db, n)
            results.append("ok")
        except RateLimitExceeded:
            results.append("limited")
    assert results.count("ok") == 100 and results.count("limited") == 5


@pytest.mark.parametrize("hour,day", [(-1, 10), (101, 10), (3, 1001), (True, 10), ("5", 10), (3.0, 10), (None, 10), (3, None)])
def test_setting_limits_outside_the_rules_is_refused_and_stores_nothing(db, hour, day):
    with pytest.raises(ValidationError):
        db.set_send_limits(hour, day, NOW, "t")
    assert db.limits_are_stored() is False and db.count_events("send_limits_set") == 0


def test_setting_limits_stores_them_and_writes_the_audit_entry(db):
    db.report_start_limits(BASE, NOW)
    assert db.set_send_limits(7, 70, NOW, "admin:web") == (7, 70) and db.limits_are_stored()
    entry = [e for e in db.audit_events() if e["event"] == "send_limits_set"][0]
    assert entry["owner"] == "admin:web" and entry["detail"] == "hour 3 -> 7, day 10 -> 70"
    db.set_send_limits(0, 70, NOW + 1, "admin:web")
    assert [e for e in db.audit_events() if e["event"] == "send_limits_set"][-1]["detail"] == "hour 7 -> 0, day 70 -> 70"


def test_the_limits_and_their_audit_entry_are_one_unit(db):
    db.set_send_limits(5, 50, NOW, "t")
    conn = sqlite3.connect(db.path)
    conn.execute("CREATE TRIGGER audit_breaks BEFORE INSERT ON audit_log WHEN NEW.event='send_limits_set' "
                 "BEGIN SELECT RAISE(ABORT, 'audit is down'); END")
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.Error):
        db.set_send_limits(99, 999, NOW + 1, "t")
    eff = stored(db)
    assert (eff.max_sends_per_hour, eff.max_sends_per_day) == (5, 50)


# -- how sends are counted ----------------------------------------------------------------------

def hour_used(db, now=NOW):
    return db.send_counts(now)[0]


def test_a_claim_counts_from_the_moment_it_is_made(db):
    d = draft(db, 1)
    db.claim_for_sending(d, "o", NOW, BASE)
    assert db.get_draft(d, "o").status == "sending" and hour_used(db) == 1


@pytest.mark.parametrize("final", ["sent", "failed", "unknown"])
def test_every_outcome_of_a_claimed_send_keeps_counting(db, final):
    d = draft(db, 1)
    db.claim_for_sending(d, "o", NOW, BASE)
    db.finish(d, final, NOW + 1)
    assert db.send_counts(NOW + 2) == (1, 1)


def test_a_send_interrupted_by_a_crash_still_counts(db):
    d = draft(db, 1)
    db.claim_for_sending(d, "o", NOW, BASE)
    assert db.recover_interrupted(NOW + 5) == 1
    assert db.get_draft(d, "o").status == "unknown" and db.send_counts(NOW + 6) == (1, 1)


def test_a_failed_send_uses_up_a_slot_so_a_loop_cannot_flood(db):
    for n in range(3):
        d = draft(db, n)
        db.claim_for_sending(d, "o", NOW, BASE)
        db.finish(d, "failed", NOW)
    with pytest.raises(RateLimitExceeded):
        claim(db, 99)


def test_refused_attempts_do_not_count(db):
    for n in range(3):
        claim(db, n)
    for n in range(10, 15):
        with pytest.raises(RateLimitExceeded):
            claim(db, n)
    assert db.send_counts(NOW) == (3, 3)
    assert db.count_events("rate_limited") == 5


def test_drafts_that_never_went_out_do_not_count(db):
    expired = db.create_draft("o", "Me", "me@example.com", "S", "b", NOW, 10, BASE).id
    cancelled = draft(db, 2)
    db.cancel(cancelled, "o", NOW)
    with pytest.raises(DraftNotSendable):
        db.claim_for_sending(expired, "o", NOW + 11, BASE)
    assert hour_used(db, NOW + 12) == 0


def test_the_hour_window_slides_and_the_day_window_keeps_counting(db):
    for n in range(3):
        claim(db, n, now=NOW)
    later = NOW + HOUR + 1
    assert db.send_counts(later) == (0, 3)
    claim(db, 50, now=later)
    assert db.send_counts(later) == (1, 4)
    assert db.send_counts(NOW + DAY + HOUR + 5) == (0, 0)


def test_the_day_limit_stops_sending_even_when_the_hour_is_free(db):
    db.set_send_limits(5, 6, NOW, "t")
    for n in range(6):
        claim(db, n, now=NOW + n * (HOUR + 1))  # never more than one per hour window
    with pytest.raises(RateLimitExceeded, match="per day"):
        claim(db, 99, now=NOW + 7 * (HOUR + 1))


def test_a_limit_of_zero_stops_every_send_and_lifting_it_restarts_them(db):
    db.set_send_limits(0, 0, NOW, "t")
    with pytest.raises(RateLimitExceeded):
        claim(db, 1)
    db.set_send_limits(2, 2, NOW + 1, "t")
    assert claim(db, 2, now=NOW + 2).status == "sending"


def test_a_changed_limit_applies_to_the_next_claim_without_a_restart(db):
    claim(db, 1)
    claim(db, 2)
    db.set_send_limits(2, 10, NOW, "t")
    with pytest.raises(RateLimitExceeded):
        claim(db, 3)
    db.set_send_limits(4, 10, NOW, "t")
    assert claim(db, 4).status == "sending"


def test_stored_limits_beat_the_starting_values_of_the_mail_service(db):
    db.set_send_limits(6, 60, NOW, "t")
    for n in range(6):
        claim(db, n)  # BASE alone would stop at 3
    with pytest.raises(RateLimitExceeded):
        claim(db, 99)


# -- parallel attempts --------------------------------------------------------------------------

def run_parallel(db, count, base=BASE):
    ids = [draft(db, n) for n in range(count)]
    barrier = threading.Barrier(count)
    results = []

    def attempt(d):
        barrier.wait()
        try:
            db.claim_for_sending(d, "o", NOW, base)
            results.append("claimed")
        except RateLimitExceeded:
            results.append("limited")

    threads = [threading.Thread(target=attempt, args=(d,)) for d in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_parallel_claims_never_pass_the_hour_limit(db):
    db.set_send_limits(5, 50, NOW, "t")
    results = run_parallel(db, 20)
    assert results.count("claimed") == 5 and results.count("limited") == 15
    assert db.send_counts(NOW) == (5, 5)


def test_parallel_claims_never_pass_the_day_limit(db):
    db.set_send_limits(50, 7, NOW, "t")
    results = run_parallel(db, 20)
    assert results.count("claimed") == 7 and db.send_counts(NOW) == (7, 7)


def test_parallel_claims_never_pass_the_hard_ceiling(db):
    conn = sqlite3.connect(db.path)
    for key in ("send_limit_per_hour", "send_limit_per_day"):
        conn.execute("INSERT INTO settings(key,value,changed_at,changed_by) VALUES (?,'100000',1,'x')", (key,))
    conn.commit()
    conn.close()
    results = run_parallel(db, 110)
    assert results.count("claimed") == 100


# -- admin API ---------------------------------------------------------------------------------------

class FakeClock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def admin_config(tmp_path):
    return AdminConfig(
        db_path=tmp_path / "mail.db", passphrase_hash=hash_password(PASSPHRASE, n=2**4), allowed_hosts=("testserver",),
    )


def make_client(admin_config, clock):
    return TestClient(build_admin_app(admin_config, clock), raise_server_exceptions=False)


def sign_in(client):
    return {"Authorization": "Bearer " + client.post("/admin/login", json={"passphrase": PASSPHRASE}).json()["token"]}


@pytest.fixture
def client(admin_config, clock):
    """Admin client; the mail service has reported its starting values (3 per hour, 10 per day)."""
    Database(admin_config.db_path).report_start_limits(BASE, NOW)
    with make_client(admin_config, clock) as c:
        yield c


@pytest.fixture
def unreported_client(admin_config, clock):
    with make_client(admin_config, clock) as c:
        yield c


@pytest.fixture
def auth(client):
    return sign_in(client)


def put(client, auth, per_hour, per_day, **extra):
    return client.put("/admin/limits", json={"per_hour": per_hour, "per_day": per_day, **extra}, headers=auth)


def raise_to(client, auth, per_hour, per_day):
    first = put(client, auth, per_hour, per_day).json()
    assert first["status"] == "confirmation_required", first
    return put(client, auth, per_hour, per_day, confirmation_id=first["confirmation_id"])


def view(client, auth):
    return client.get("/admin/limits", headers=auth).json()


def test_view_shows_effective_values_source_ceilings_and_use(client, auth, admin_config, clock):
    claim(Database(admin_config.db_path), 1, now=clock.t - 10)
    assert view(client, auth) == {
        "per_hour": 3, "per_day": 10, "source": "mail_service_settings",
        "starting_values": {"per_hour": 3, "per_day": 10},
        "ceiling": {"per_hour": 100, "per_day": 1000}, "used": {"last_hour": 1, "last_day": 1},
    }
    assert client.get("/admin/limits").status_code == 401


def test_the_admin_settings_file_has_no_say_over_the_displayed_values(admin_config, clock, tmp_path):
    env = {"OUTBOX_DB_PATH": str(admin_config.db_path), "OUTBOX_ADMIN_PASSPHRASE_HASH": admin_config.passphrase_hash,
           "OUTBOX_MAX_SENDS_PER_HOUR": "77", "OUTBOX_MAX_SENDS_PER_DAY": "777"}
    cfg = AdminConfig.from_env(env)
    assert not hasattr(cfg, "start_sends_per_hour")
    Database(admin_config.db_path).report_start_limits(BASE, NOW)
    cfg = AdminConfig(db_path=cfg.db_path, passphrase_hash=cfg.passphrase_hash, allowed_hosts=("testserver",))
    with make_client(cfg, clock) as c:
        shown = view(c, sign_in(c))
    assert (shown["per_hour"], shown["per_day"]) == (3, 10)


def test_before_any_report_the_values_are_unknown_not_guessed(unreported_client):
    h = sign_in(unreported_client)
    shown = view(unreported_client, h)
    assert (shown["per_hour"], shown["per_day"], shown["source"], shown["starting_values"]) == (None, None, "unknown", None)


def test_with_unknown_values_any_increase_needs_confirmation_but_stopping_does_not(unreported_client):
    h = sign_in(unreported_client)
    first = put(unreported_client, h, 5, 5).json()
    assert first["status"] == "confirmation_required" and first["preview"] == {"per_hour": "unknown -> 5", "per_day": "unknown -> 5"}
    assert put(unreported_client, h, 0, 0).json()["status"] == "done"
    assert view(unreported_client, h)["source"] == "stored"


def test_the_service_reports_what_it_really_runs_with(admin_config, clock, tmp_path, sender):
    """georgie case: the mail service starts with 10 per hour and 200 per day; the admin side shows exactly that."""
    import dataclasses
    from outbox_mcp.config import Config

    cfg = Config(
        smtp_host="localhost", smtp_port=2525, smtp_user="", smtp_password="", smtp_security="none",
        from_address="me@example.com", from_name="Me", recipients_file=tmp_path / "r.json", db_path=admin_config.db_path,
        max_sends_per_hour=10, max_sends_per_day=200,
    )
    db = Database(cfg.db_path)
    OutboxService(cfg, DbRecipientList(RecipientStore(db)), db, sender, clock, recipient_store=RecipientStore(db))
    with make_client(admin_config, clock) as c:
        h = sign_in(c)
        shown = view(c, h)
        assert (shown["per_hour"], shown["per_day"], shown["source"]) == (10, 200, "mail_service_settings")
        # a restart with other starting values is reflected, and values above the ceilings are shown cut down
        cfg2 = dataclasses.replace(cfg, max_sends_per_hour=5000, max_sends_per_day=50)
        OutboxService(cfg2, DbRecipientList(RecipientStore(db)), db, sender, clock, recipient_store=RecipientStore(db))
        shown = view(c, h)
        assert (shown["per_hour"], shown["per_day"]) == (100, 50)


def test_a_stored_value_wins_over_the_report_and_the_report_stays_visible_as_starting_values(client, auth, admin_config):
    put(client, auth, 2, 5)
    shown = view(client, auth)
    assert (shown["per_hour"], shown["per_day"], shown["source"]) == (2, 5, "stored")
    assert shown["starting_values"] == {"per_hour": 3, "per_day": 10}
    Database(admin_config.db_path).report_start_limits(RateLimits(1, 50, 500), NOW + 5)  # service restarted with new file
    shown = view(client, auth)
    assert (shown["per_hour"], shown["per_day"], shown["source"]) == (2, 5, "stored")
    assert shown["starting_values"] == {"per_hour": 50, "per_day": 500}


def test_the_displayed_values_are_the_ones_the_mail_service_enforces(client, auth, admin_config):
    db = Database(admin_config.db_path)
    for n in range(3):
        claim(db, n)  # reported start: 3 per hour
    with pytest.raises(RateLimitExceeded):
        claim(db, 50)
    assert raise_to(client, auth, 4, 10).json()["status"] == "done"
    assert (view(client, auth)["per_hour"], view(client, auth)["source"]) == (4, "stored")
    assert claim(db, 51).status == "sending"
    with pytest.raises(RateLimitExceeded):
        claim(db, 52)


def test_reporting_the_same_values_again_writes_nothing(admin_config):
    db = Database(admin_config.db_path)
    db.report_start_limits(BASE, NOW)
    conn = sqlite3.connect(db.path)
    stamp = conn.execute("SELECT changed_at FROM settings WHERE key='send_limits_start'").fetchone()[0]
    conn.close()
    db.report_start_limits(BASE, NOW + 99)
    conn = sqlite3.connect(db.path)
    assert conn.execute("SELECT changed_at FROM settings WHERE key='send_limits_start'").fetchone()[0] == stamp
    conn.close()


def test_the_report_is_information_only_and_never_decides_a_send(admin_config):
    db = Database(admin_config.db_path)
    db.report_start_limits(RateLimits(1, 100, 1000), NOW)  # claims a generous start ...
    for n in range(3):
        claim(db, n)  # ... but the service's own base limit (3 per hour) is what counts
    with pytest.raises(RateLimitExceeded):
        claim(db, 9)


def test_lowering_is_immediate_and_logged(client, auth, admin_config):
    r = put(client, auth, 2, 5)
    assert r.status_code == 200 and r.json()["status"] == "done" and (r.json()["per_hour"], r.json()["per_day"]) == (2, 5)
    assert r.json()["source"] == "stored"
    entry = [e for e in Database(admin_config.db_path).audit_events() if e["event"] == "send_limits_set"][-1]
    assert entry["owner"] == "admin:web" and entry["detail"] == "hour 3 -> 2, day 10 -> 5"


def test_stopping_all_sending_needs_no_confirmation(client, auth):
    assert put(client, auth, 0, 0).json()["status"] == "done"


def test_raising_asks_first_and_changes_nothing_until_confirmed(client, auth, admin_config):
    first = put(client, auth, 100, 1000).json()
    assert first["status"] == "confirmation_required" and first["preview"] == {"per_hour": "3 -> 100", "per_day": "10 -> 1000"}
    assert Database(admin_config.db_path).limits_are_stored() is False
    done = put(client, auth, 100, 1000, confirmation_id=first["confirmation_id"]).json()
    assert done["status"] == "done" and (done["per_hour"], done["per_day"]) == (100, 1000)


def test_raising_one_value_while_lowering_the_other_still_asks(client, auth):
    assert put(client, auth, 1, 50).json()["status"] == "confirmation_required"


def test_the_confirmation_is_single_use_bound_and_void_after_a_change(client, auth, clock):
    cid = put(client, auth, 50, 500).json()["confirmation_id"]
    assert put(client, auth, 60, 500, confirmation_id=cid).status_code == 409  # other target; used up
    assert put(client, auth, 50, 500, confirmation_id=cid).status_code == 409
    cid = put(client, auth, 50, 500).json()["confirmation_id"]
    put(client, auth, 1, 1)  # limits changed meanwhile
    assert put(client, auth, 50, 500, confirmation_id=cid).status_code == 409
    cid = put(client, auth, 50, 500).json()["confirmation_id"]
    clock.t += 121
    assert put(client, sign_in(client), 50, 500, confirmation_id=cid).status_code == 409


@pytest.mark.parametrize("hour,day", [(101, 10), (3, 1001), (-1, 10), ("3", 10), (3.5, 10), (True, 10), (None, 10)])
def test_values_outside_the_rules_are_refused(client, auth, hour, day, admin_config):
    assert put(client, auth, hour, day).status_code == 400
    assert Database(admin_config.db_path).limits_are_stored() is False


def test_the_ceiling_cannot_be_raised_even_with_a_confirmation(client, auth):
    assert put(client, auth, 100, 1001).status_code == 400
    assert client.put("/admin/limits", json={"per_hour": 100}, headers=auth).status_code == 400
    assert client.put("/admin/limits", json={}, headers=auth).status_code == 400


def test_repeating_the_stored_values_changes_nothing(client, auth, admin_config):
    put(client, auth, 2, 5)
    before = len(Database(admin_config.db_path).audit_events())
    assert put(client, auth, 2, 5).json()["status"] == "unchanged"
    assert len(Database(admin_config.db_path).audit_events()) == before


def test_saving_the_current_starting_values_stores_them_explicitly(client, auth, admin_config):
    assert put(client, auth, 3, 10).json()["status"] == "done"
    assert Database(admin_config.db_path).limits_are_stored() is True
    assert view(client, auth)["source"] == "stored"


def test_a_failing_audit_leaves_the_limits_unchanged(client, auth, admin_config):
    put(client, auth, 2, 5)
    conn = sqlite3.connect(admin_config.db_path)
    conn.execute("CREATE TRIGGER audit_breaks BEFORE INSERT ON audit_log WHEN NEW.event='send_limits_set' "
                 "BEGIN SELECT RAISE(ABORT, 'audit is down'); END")
    conn.commit()
    conn.close()
    r = put(client, auth, 1, 1)
    assert r.status_code == 500 and r.json()["error"] == "internal"
    assert view(client, auth)["per_hour"] == 2 and view(client, auth)["per_day"] == 5


def test_limits_set_through_the_api_govern_the_mail_service(client, auth, admin_config):
    db = Database(admin_config.db_path)
    assert raise_to(client, auth, 6, 60).json()["status"] == "done"
    for n in range(6):
        claim(db, n)  # the mail service's own starting value would stop at 3
    with pytest.raises(RateLimitExceeded):
        claim(db, 99)
    assert put(client, auth, 2, 60).json()["status"] == "done"
    with pytest.raises(RateLimitExceeded):
        claim(db, 100)
    assert view(client, auth)["used"]["last_hour"] == 6
