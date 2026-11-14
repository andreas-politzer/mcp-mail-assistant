"""Parallel access: the limits must hold when requests arrive at the same moment.

Each worker thread uses its own TestClient (own cookie jar), like separate browsers.
"""

import dataclasses
import threading
import time

from starlette.testclient import TestClient

from auth_helpers import ISSUER, PASSWORD, FakeClock, Flow, add_ping
from outbox_mcp.auth.app import create_authenticated_app
from outbox_mcp.auth.store import AuthStore, ClientLimitReached, PendingLimitReached


def run_parallel(count, target):
    """Run target(i) in `count` threads, all released at the same moment."""
    barrier = threading.Barrier(count)
    results = [None] * count

    def worker(i):
        barrier.wait()
        results[i] = target(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def make_app(auth_config, tmp_path, clock, **changes):
    cfg = dataclasses.replace(auth_config, db_path=tmp_path / "par.db", **changes)
    store = AuthStore(cfg.db_path, clock=clock)
    return cfg, store, create_authenticated_app(cfg, store, configure=add_ping, json_response=True)


def open_logins(app, store, clock, count):
    """Create `count` pending logins; returns (request_id, csrf, client) per login."""
    prepared = []
    setup = TestClient(app, base_url=ISSUER, follow_redirects=False)
    flow = Flow(setup, store, clock)
    cid = flow.new_client_id()
    for _ in range(count):
        _, challenge = flow.pkce()
        rid = flow.request_id(flow.authorize(cid, challenge))
        c = TestClient(app, base_url=ISSUER, follow_redirects=False)
        f = Flow(c, store, clock)
        prepared.append((rid, f.csrf_of(f.login_page(rid)), f))
    return prepared


# --- the brute-force limit must be atomic -------------------------------------------

def test_parallel_wrong_guesses_never_exceed_the_limit(auth_config, tmp_path, clock):
    cfg, store, app = make_app(auth_config, tmp_path, clock, max_clients=100, max_pending_requests=100)
    logins = open_logins(app, store, clock, 24)

    def attempt(i):
        rid, csrf, f = logins[i]
        return f.login(rid, "wrong-guess", csrf=csrf).status_code

    codes = run_parallel(24, attempt)
    assert codes.count(401) == cfg.max_login_failures
    assert codes.count(429) == 24 - cfg.max_login_failures
    assert store.login_failure_count() == cfg.max_login_failures
    assert store.counts()["auth_codes"] == 0


def test_parallel_guesses_cannot_sneak_the_right_password_past_the_limit(auth_config, tmp_path, clock):
    cfg, store, app = make_app(auth_config, tmp_path, clock, max_clients=100, max_pending_requests=100)
    logins = open_logins(app, store, clock, 12)
    for rid, csrf, f in logins[:cfg.max_login_failures]:
        assert f.login(rid, "wrong", csrf=csrf).status_code == 401
    rid, csrf, f = logins[-1]
    assert f.login(rid, PASSWORD, csrf=csrf).status_code == 429
    assert store.counts()["auth_codes"] == 0


def test_only_a_few_expensive_password_checks_run_at_once(auth_config, tmp_path, clock, monkeypatch):
    cfg, store, app = make_app(
        auth_config, tmp_path, clock, max_login_failures=50, max_clients=100, max_pending_requests=100)
    state = {"now": 0, "peak": 0}
    guard = threading.Lock()

    def slow_verify(password, stored):
        with guard:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        with guard:
            state["now"] -= 1
        return False

    monkeypatch.setattr("outbox_mcp.auth.web.verify_password", slow_verify)
    logins = open_logins(app, store, clock, 12)

    def attempt(i):
        rid, csrf, f = logins[i]
        return f.login(rid, "x", csrf=csrf).status_code

    codes = run_parallel(12, attempt)
    assert set(codes) == {401}
    assert 1 <= state["peak"] <= cfg.max_parallel_password_checks


def test_a_successful_login_does_not_release_checks_that_are_still_running(auth_config, tmp_path, clock, monkeypatch):
    from outbox_mcp.auth import web
    from outbox_mcp.auth.passwords import verify_password as real_verify

    cfg, store, app = make_app(
        auth_config, tmp_path, clock, max_login_failures=2, max_clients=100, max_pending_requests=100)
    started = threading.Event()

    def verify(password, stored):
        if password == "slow":
            started.set()
            time.sleep(0.6)
            return False
        return real_verify(password, stored)

    monkeypatch.setattr(web, "verify_password", verify)
    a, b, c, d = open_logins(app, store, clock, 4)
    slow_result = []

    def slow_attempt():
        rid, csrf, f = a
        slow_result.append(f.login(rid, "slow", csrf=csrf).status_code)

    t = threading.Thread(target=slow_attempt)
    t.start()
    assert started.wait(5)
    assert b[2].login(b[0], PASSWORD, csrf=b[1]).status_code == 303  # owner logs in meanwhile
    first = c[2].login(c[0], "wrong", csrf=c[1]).status_code
    second = d[2].login(d[0], "wrong", csrf=d[1]).status_code
    t.join()
    # Limit is 2: the still-running check keeps one slot, so only one more guess fits.
    assert (first, second) == (401, 429)
    assert slow_result == [401]


# --- codes and tokens: exactly one winner -------------------------------------------

def _code(store):
    return store.create_code(
        client_id="c", scopes=["mail:send"], code_challenge="x", redirect_uri="https://claude.ai/cb",
        redirect_explicit=True, resource="r", subject="o", ttl=100)


def test_a_code_can_be_redeemed_only_once_even_in_parallel(store):
    code = _code(store)
    results = run_parallel(8, lambda i: store.redeem_code(code, "c"))
    assert sum(r is not None for r in results) == 1


def _pair(store):
    family = store.new_family("c")
    return store.issue_tokens(family_id=family, client_id="c", scopes=["mail:send"], resource="r", subject="o")


def test_a_refresh_token_can_be_rotated_only_once_even_in_parallel(store):
    issued = _pair(store)
    results = run_parallel(6, lambda i: store.rotate_refresh(issued.refresh_token, "c", ["mail:send"]))
    assert sum(r is not None for r in results) == 1


def test_documented_strict_behaviour_parallel_refresh_ends_the_session(store):
    """Strict rotation is a deliberate choice for now (see docs/oauth-security.md).

    If the same refresh token arrives twice at once, the whole family is revoked, so even
    the winner's new tokens stop working. Changing this (a grace window) must be a decision
    made after watching how Claude really refreshes, and then this test changes with it."""
    issued = _pair(store)
    results = run_parallel(4, lambda i: store.rotate_refresh(issued.refresh_token, "c", ["mail:send"]))
    winner = next(r for r in results if r is not None)
    assert store.lookup_access(winner.access_token) is None


# --- registration limits ------------------------------------------------------------

def test_parallel_registrations_respect_the_client_cap(store, auth_config):
    from mcp.shared.auth import OAuthClientInformationFull

    def register(i):
        info = OAuthClientInformationFull(
            client_id=f"client-{i}", redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
            token_endpoint_auth_method="none")
        try:
            store.register_client(info, max_clients=5, prune_before=0)
            return True
        except ClientLimitReached:
            return False

    results = run_parallel(16, register)
    assert sum(results) == 5
    assert store.counts()["clients"] == 5


def test_parallel_login_starts_respect_the_pending_cap(store):
    def start(i):
        try:
            store.create_pending("c", {"x": 1}, ttl=600, max_pending=4)
            return True
        except PendingLimitReached:
            return False

    results = run_parallel(12, start)
    assert sum(results) == 4
    assert store.counts()["pending_requests"] == 4
