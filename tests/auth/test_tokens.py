"""Token lifetimes, refresh-token rotation and reuse detection."""

import pytest

DAY = 86400


def test_refresh_rotates_both_tokens(flow):
    t = flow.full_login()
    resp = flow.refresh(t["client_id"], t["refresh"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] != t["access"]
    assert body["refresh_token"] != t["refresh"]
    assert body["expires_in"] == 3600
    assert flow.mcp_initialize(body["access_token"]).status_code == 200


def test_old_refresh_token_is_dead_and_its_reuse_kills_the_family(flow):
    t = flow.full_login()
    new = flow.refresh(t["client_id"], t["refresh"]).json()
    replay = flow.refresh(t["client_id"], t["refresh"])
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    # Replay of a rotated token means it may have leaked: the whole family goes.
    assert flow.refresh(t["client_id"], new["refresh_token"]).status_code == 400
    assert flow.mcp_initialize(new["access_token"]).status_code == 401


def test_refresh_token_belongs_to_one_client(flow):
    t = flow.full_login()
    other = flow.new_client_id()
    assert flow.refresh(other, t["refresh"]).status_code == 400
    # The legitimate client is not harmed by somebody else's attempt.
    assert flow.refresh(t["client_id"], t["refresh"]).status_code == 200


def test_refresh_token_expires_after_90_days_of_inactivity(flow, clock):
    t = flow.full_login()
    clock.advance(90 * DAY + 1)
    resp = flow.refresh(t["client_id"], t["refresh"])
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_refresh_token_is_still_good_just_before_90_days(flow, clock):
    t = flow.full_login()
    clock.advance(89 * DAY)
    assert flow.refresh(t["client_id"], t["refresh"]).status_code == 200


def test_the_90_day_window_slides_with_use(flow, clock):
    t = flow.full_login()
    clock.advance(60 * DAY)
    second = flow.refresh(t["client_id"], t["refresh"])
    assert second.status_code == 200
    clock.advance(60 * DAY)
    assert flow.refresh(t["client_id"], second.json()["refresh_token"]).status_code == 200


def test_access_token_lasts_one_hour(flow, clock):
    t = flow.full_login()
    clock.advance(3500)
    assert flow.mcp_initialize(t["access"]).status_code == 200
    clock.advance(200)
    assert flow.mcp_initialize(t["access"]).status_code == 401


def test_an_expired_access_token_can_be_replaced_by_refreshing(flow, clock):
    t = flow.full_login()
    clock.advance(3700)
    assert flow.mcp_initialize(t["access"]).status_code == 401
    body = flow.refresh(t["client_id"], t["refresh"]).json()
    assert flow.mcp_initialize(body["access_token"]).status_code == 200


def test_scope_cannot_be_widened_by_refreshing(flow):
    t = flow.full_login()
    resp = flow.client.post("/token", data={
        "grant_type": "refresh_token", "refresh_token": t["refresh"],
        "client_id": t["client_id"], "scope": "mail:send vault:read",
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_scope"


def test_tokens_are_not_stored_in_plaintext(flow, auth_config):
    t = flow.full_login()
    raw = auth_config.db_path.read_bytes()
    assert t["access"].encode() not in raw
    assert t["refresh"].encode() not in raw


@pytest.mark.parametrize("garbage", ["x", "a" * 200, "oat_" + "0" * 43, "Bearer"])
def test_made_up_tokens_are_refused(flow, garbage):
    assert flow.mcp_initialize(garbage).status_code == 401
