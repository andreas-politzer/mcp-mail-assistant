"""A1: the local approval page (http://localhost:<port>) for the stdio server. Same checks as the
public origin; if the page cannot start, approval stays off and external mail stays unsendable."""

import json
import socket
import urllib.error
import urllib.request

import pytest

from approval_helpers import Device
from outbox_mcp.approval.local_listener import start_local_listener
from outbox_mcp.approval.service import ApprovalConfig, ApprovalService
from outbox_mcp.errors import ConfigError
from outbox_mcp.server import attach_local_approval
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore

EXT = "ferdinand@example.org"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_service(config, sender, port, db=None, clock=None):
    import time

    clock = clock or time.time
    db = db or Database(config.db_path)
    store = RecipientStore(db)
    if not store.has_any():
        store.add("Me", "me@example.com", ["myself"], 1.0, "admin:test", scope="own")
        store.add("Ferdinand", EXT, [], 1.0, "admin:test")
    approval = ApprovalService(db, ApprovalConfig(rp_id="localhost", port=port), clock)
    return OutboxService(config, DbRecipientList(store), db, sender, clock, recipient_store=store, approval=approval)


def http(method, port, path, host=None, origin=None, body=None):
    headers = {"Host": host or f"localhost:{port}"}
    if origin:
        headers["Origin"] = origin
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# -- configuration -------------------------------------------------------------------------

def test_local_origin_has_scheme_and_port():
    c = ApprovalConfig(rp_id="localhost", port=8433)
    assert c.is_local and c.origin == "http://localhost:8433"
    assert ApprovalConfig(rp_id="relay.example.org").origin == "https://relay.example.org"


@pytest.mark.parametrize("kw", [
    dict(rp_id="localhost"), dict(rp_id="localhost", port=80), dict(rp_id="localhost", port=70000),
    dict(rp_id="localhost", port=True), dict(rp_id="relay.example.org", port=8433),
    dict(rp_id="127.0.0.1", port=8433), dict(rp_id="localhost:8433", port=8433),
    dict(rp_id="foo.localhost", port=8433), dict(rp_id="[::1]", port=8433),
])
def test_only_exact_localhost_with_a_port_may_use_http(kw):
    with pytest.raises(ConfigError):
        ApprovalConfig(**kw)


def test_env_reads_the_port():
    c = ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": "localhost", "OUTBOX_APPROVAL_PORT": "8433"})
    assert c.origin == "http://localhost:8433"
    with pytest.raises(ConfigError):
        ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": "localhost", "OUTBOX_APPROVAL_PORT": "abc"})
    with pytest.raises(ConfigError):
        ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": "relay.example.org", "OUTBOX_APPROVAL_PORT": "8433"})


def test_the_http_server_refuses_the_local_origin(monkeypatch, tmp_path, config):
    from outbox_mcp import http_server

    monkeypatch.setattr(http_server, "AuthConfig", type("A", (), {"from_env": staticmethod(lambda e: object())}))
    monkeypatch.setattr(http_server, "build_service", lambda c, e: type("S", (), {"approval": type("P", (), {"config": ApprovalConfig(rp_id="localhost", port=8433)})()})())
    monkeypatch.setattr(http_server.Config, "from_env", staticmethod(lambda e: config))
    with pytest.raises(ConfigError):
        http_server.build_app_from_env({})


# -- the listener ---------------------------------------------------------------------------

@pytest.fixture
def local(config, sender):
    port = free_port()
    svc = make_service(config, sender, port)
    handle = start_local_listener(svc.approval)
    assert handle.error is None
    # wait until it answers
    import time
    for _ in range(50):
        try:
            http("GET", port, "/approval/style.css")
            break
        except OSError:
            time.sleep(0.1)
    yield svc, handle, port
    handle.stop()


def test_listener_binds_to_loopback_only(local):
    svc, handle, port = local
    assert handle.running
    # an external interface address must not answer
    other = [a for a in socket.gethostbyname_ex(socket.gethostname())[2] if not a.startswith("127.")]
    for addr in other:
        with socket.socket() as s:
            s.settimeout(1)
            assert s.connect_ex((addr, port)) != 0


def test_pages_load_under_the_local_host_and_refuse_everything_else(local):
    svc, handle, port = local
    token = svc.approval.create_enrollment()
    path = f"/passkey/enroll/{token}"
    assert http("GET", port, path)[0] == 200
    for bad in ("127.0.0.1:%d" % port, "localhost", "localhost:%d" % (port + 1), "evil.example", "localhost.evil.example:%d" % port):
        assert http("GET", port, path, host=bad)[0] == 421, bad
    assert http("GET", port, "/approval/app.js", host="evil.example")[0] == 421


def test_posts_need_the_exact_local_origin(local):
    svc, handle, port = local
    token = svc.approval.create_enrollment()
    p = f"/passkey/enroll/{token}/options"
    good = f"http://localhost:{port}"
    assert http("POST", port, p, origin=good, body={})[0] == 200
    for bad in (None, "http://localhost", f"http://127.0.0.1:{port}", f"http://localhost:{port + 1}", f"https://localhost:{port}", "http://evil.example"):
        token = svc.approval.create_enrollment()
        assert http("POST", port, f"/passkey/enroll/{token}/options", origin=bad, body={})[0] == 400, bad


def test_full_approval_flow_over_the_local_page_with_a_real_signature(local, config):
    svc, handle, port = local
    origin = f"http://localhost:{port}"
    dev = Device(rp_id="localhost", origin=origin)
    token = svc.approval.create_enrollment()
    code, body = http("POST", port, f"/passkey/enroll/{token}/options", origin=origin, body={})
    cid = json.loads(body)["challenge_id"]
    code, body = http("POST", port, f"/passkey/enroll/{token}/verify", origin=origin,
                      body={"challenge_id": cid, "credential": dev.register(cid)})
    assert code == 200, body
    d = svc.prepare_email("o", "Ferdinand", "S", "T")
    assert d["approval_url"] == f"{origin}/approve/{d['draft_id']}"
    assert svc.send_email("o", d["draft_id"])["status"] == "approval_required"
    code, body = http("POST", port, f"/approve/{d['draft_id']}/options", origin=origin, body={})
    cid = json.loads(body)["challenge_id"]
    code, body = http("POST", port, f"/approve/{d['draft_id']}/verify", origin=origin,
                      body={"challenge_id": cid, "credential": dev.assert_(cid)})
    assert code == 200, body
    assert svc.send_email("o", d["draft_id"])["status"] == "sent"
    # the origin of the PUBLIC setup does not work on the local page
    d2 = svc.prepare_email("o", "Ferdinand", "S2", "T2")
    code, body = http("POST", port, f"/approve/{d2['draft_id']}/options", origin=origin, body={})
    cid = json.loads(body)["challenge_id"]
    code, _ = http("POST", port, f"/approve/{d2['draft_id']}/verify", origin=origin,
                   body={"challenge_id": cid, "credential": dev.assert_(cid, origin="https://relay.example.org")})
    assert code == 400


def test_nothing_is_written_to_standard_output(config, sender, capsys):
    svc = make_service(config, sender, free_port())
    handle = start_local_listener(svc.approval)
    try:
        http("GET", handle.port, "/approval/style.css")
        http("GET", handle.port, "/approval/style.css", host="evil.example")
    finally:
        handle.stop()
    assert capsys.readouterr().out == ""


# -- port conflicts and several instances --------------------------------------------------------

def free_block(n):
    """The first port of n consecutive free loopback ports."""
    import random

    for _ in range(200):
        first = random.randint(20000, 60000)
        socks = []
        try:
            for p in range(first, first + n):
                s = socket.socket()
                s.bind(("127.0.0.1", p))
                socks.append(s)
            return first
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("no free port block")


def occupy(first, n):
    socks = []
    for p in range(first, first + n):
        s = socket.socket()
        s.bind(("127.0.0.1", p))
        s.listen(1)
        socks.append(s)
    return socks


def wait_until_up(port):
    import time

    for _ in range(50):
        try:
            http("GET", port, "/approval/style.css")
            return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"port {port} never answered")


def enroll_and_approve(svc, port, dev, draft_port=None):
    """Enroll a passkey on `port` (if dev has none yet), then prepare and approve a draft on svc."""
    origin = f"http://localhost:{port}"
    dev.origin = origin
    if not svc.approval.passkey_count():
        token = svc.approval.create_enrollment()
        _, body = http("POST", port, f"/passkey/enroll/{token}/options", origin=origin, body={})
        cid = json.loads(body)["challenge_id"]
        assert http("POST", port, f"/passkey/enroll/{token}/verify", origin=origin,
                    body={"challenge_id": cid, "credential": dev.register(cid)})[0] == 200
    d = svc.prepare_email("o", "Ferdinand", "S", "T")
    assert d["approval_url"] == f"{origin}/approve/{d['draft_id']}"
    _, body = http("POST", port, f"/approve/{d['draft_id']}/options", origin=origin, body={})
    cid = json.loads(body)["challenge_id"]
    code, body = http("POST", port, f"/approve/{d['draft_id']}/verify", origin=origin,
                      body={"challenge_id": cid, "credential": dev.assert_(cid)})
    assert code == 200, body
    return d


def test_a_taken_port_moves_the_page_to_the_next_free_port(config, sender, capsys):
    first = free_block(3)
    squatters = occupy(first, 1)
    svc = make_service(config, sender, first)
    original = svc.approval.config
    try:
        attach_local_approval(svc)
        assert svc.approval is not None
        handle_port = svc.approval.config.port
        assert handle_port == first + 1                       # the real port, not the configured one
        assert svc.approval.config.origin == f"http://localhost:{first + 1}"
        assert original.port == first                          # the configuration object itself is untouched
        wait_until_up(first + 1)
        d = enroll_and_approve(svc, first + 1, Device(rp_id="localhost", origin=f"http://localhost:{first + 1}"))
        assert len(sender.sent) == 1                           # the page sent it right after the approval
        assert svc.draft_status("o", d["draft_id"])["status"] == "sent"
    finally:
        for s in squatters:
            s.close()
    captured = capsys.readouterr()
    assert captured.out == ""                                  # standard output stays the protocol channel
    assert f"port {first} is in use" in captured.err


def test_the_configured_port_is_kept_when_it_is_free(config, sender, capsys):
    first = free_block(2)
    svc = make_service(config, sender, first)
    handle = start_local_listener(svc.approval)
    try:
        assert handle.error is None and handle.port == first
        assert svc.approval.config.port == first
        wait_until_up(first)
    finally:
        handle.stop()
    assert capsys.readouterr().err == ""                       # nothing to report, nothing reported


def test_the_origin_check_uses_the_real_port_after_a_move(config, sender):
    first = free_block(3)
    squatters = occupy(first, 1)
    svc = make_service(config, sender, first)
    handle = start_local_listener(svc.approval)
    port = first + 1
    try:
        assert handle.error is None and handle.port == port
        wait_until_up(port)
        token = svc.approval.create_enrollment()
        good = f"http://localhost:{port}"
        assert http("POST", port, f"/passkey/enroll/{token}/options", origin=good, body={})[0] == 200
        for bad in (f"http://localhost:{first}", f"http://localhost:{port + 1}", "http://localhost",
                    f"http://127.0.0.1:{port}", f"https://localhost:{port}"):
            token = svc.approval.create_enrollment()
            assert http("POST", port, f"/passkey/enroll/{token}/options", origin=bad, body={})[0] == 400, bad
        for bad_host in (f"localhost:{first}", f"localhost:{port + 1}", "localhost", f"127.0.0.1:{port}"):
            assert http("GET", port, f"/passkey/enroll/{token}", host=bad_host)[0] == 421, bad_host
        # a signature made for the configured (busy) port's origin is refused on the real port
        dev = Device(rp_id="localhost", origin=good)
        token = svc.approval.create_enrollment()
        _, body = http("POST", port, f"/passkey/enroll/{token}/options", origin=good, body={})
        cid = json.loads(body)["challenge_id"]
        assert http("POST", port, f"/passkey/enroll/{token}/verify", origin=good,
                    body={"challenge_id": cid, "credential": dev.register(cid, origin=f"http://localhost:{first}")})[0] == 400
    finally:
        handle.stop()
        for s in squatters:
            s.close()


def test_every_port_in_the_range_taken_means_no_listener_and_approval_stays_off(config, sender, capsys):
    from outbox_mcp.approval.local_listener import PORT_TRIES

    first = free_block(PORT_TRIES + 1)
    squatters = occupy(first, PORT_TRIES)
    try:
        svc = make_service(config, sender, first)
        handle = start_local_listener(svc.approval)
        assert handle.error and not handle.running
        assert svc.approval.config.port == first                # nothing was moved
        attach_local_approval(svc)
    finally:
        for s in squatters:
            s.close()
    assert svc.approval is None
    captured = capsys.readouterr()
    assert captured.out == "" and "approval page not started" in captured.err
    d = svc.prepare_email("o", "Ferdinand", "S", "T")
    assert d["approval_required"] is True and d["approval_url"] is None
    assert svc.send_email("o", d["draft_id"])["status"] == "approval_required"  # fails closed, nothing sent


def test_the_port_after_the_range_is_not_tried(config, sender):
    from outbox_mcp.approval.local_listener import PORT_TRIES

    first = free_block(PORT_TRIES + 1)
    squatters = occupy(first, PORT_TRIES)
    after = socket.socket()
    after.bind(("127.0.0.1", first + PORT_TRIES))               # free, but outside the range
    after.close()
    try:
        svc = make_service(config, sender, first)
        handle = start_local_listener(svc.approval)
        assert handle.error and not handle.running
    finally:
        for s in squatters:
            s.close()


def test_the_range_stops_at_the_last_valid_port(config, sender):
    from outbox_mcp.approval.local_listener import _bind_first_free

    sock, port, exc = _bind_first_free(65535)
    try:
        assert port == 65535 or (sock is None and exc is not None)
    finally:
        if sock is not None:
            sock.close()


def test_two_instances_started_with_the_same_port_both_work(config, sender):
    """What Claude Desktop does: two servers from the same configuration, one data folder."""
    first = free_block(4)
    db = Database(config.db_path)
    a = make_service(config, sender, first, db)
    b = make_service(config, sender, first, db)
    ha = start_local_listener(a.approval)
    hb = start_local_listener(b.approval)
    try:
        assert ha.error is None and hb.error is None
        assert {ha.port, hb.port} == {first, first + 1} and ha.port != hb.port
        assert a.approval.config.port == ha.port and b.approval.config.port == hb.port
        wait_until_up(ha.port); wait_until_up(hb.port)
        dev = Device(rp_id="localhost", origin=f"http://localhost:{ha.port}")
        # the passkey is enrolled on A's page; B (which has its own port) finds it and approves with it
        da = enroll_and_approve(a, ha.port, dev)
        assert a.send_email("o", da["draft_id"])["status"] == "sent"
        assert b.approval.passkey_count() == 1
        db_ = enroll_and_approve(b, hb.port, dev)
        assert db_["approval_url"].startswith(f"http://localhost:{hb.port}/")
        assert b.send_email("o", db_["draft_id"])["status"] == "sent"
        # each page answers only under its own port
        other = b.prepare_email("o", "Ferdinand", "S2", "T2")
        assert http("GET", ha.port, f"/approve/{other['draft_id']}", host=f"localhost:{hb.port}")[0] == 421
        assert http("GET", hb.port, f"/approve/{other['draft_id']}", host=f"localhost:{ha.port}")[0] == 421
        assert http("POST", ha.port, f"/approve/{other['draft_id']}/options",
                    origin=f"http://localhost:{hb.port}", body={})[0] == 400
        assert http("POST", hb.port, f"/approve/{other['draft_id']}/options",
                    origin=f"http://localhost:{ha.port}", body={})[0] == 400
    finally:
        ha.stop(); hb.stop()


def test_several_instances_each_get_their_own_port_and_a_new_one_still_finds_a_free_port(config, sender):
    first = free_block(5)
    db = Database(config.db_path)
    services = [make_service(config, sender, first, db) for _ in range(3)]
    handles = [start_local_listener(s.approval) for s in services]
    try:
        assert all(h.error is None for h in handles)
        assert [h.port for h in handles] == [first, first + 1, first + 2]
        for h in handles:
            wait_until_up(h.port)
        handles[0].stop()                                        # the first instance ends (Desktop closes)
        late = make_service(config, sender, first, db)
        hl = start_local_listener(late.approval)
        try:
            # never an error and never a port that is still in use; a port that just served a request
            # can stay blocked for a short time, so the new one may or may not get the freed port
            assert hl.error is None and hl.port not in (first + 1, first + 2)
            assert first <= hl.port < first + 10
            wait_until_up(hl.port)
        finally:
            hl.stop()
    finally:
        for h in handles:
            h.stop()


def test_a_port_race_between_threads_never_gives_two_listeners_the_same_port(config, sender):
    import threading

    first = free_block(8)
    db = Database(config.db_path)
    services = [make_service(config, sender, first, db) for _ in range(5)]
    handles = [None] * 5

    def go(i):
        handles[i] = start_local_listener(services[i].approval)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert all(h.error is None for h in handles)
        assert len({h.port for h in handles}) == 5               # all different
        assert all(first <= h.port < first + 8 for h in handles)
    finally:
        for h in handles:
            h.stop()


def test_two_instances_with_their_own_ports_share_passkeys_and_approvals(config, sender):
    import time

    db = Database(config.db_path)
    pa, pb = free_port(), free_port()
    a = make_service(config, sender, pa, db)
    b = make_service(config, sender, pb, db)
    ha, hb = start_local_listener(a.approval), start_local_listener(b.approval)
    try:
        assert ha.error is None and hb.error is None
        for _ in range(50):
            try:
                http("GET", pa, "/approval/style.css"); http("GET", pb, "/approval/style.css"); break
            except OSError:
                time.sleep(0.1)
        dev = Device(rp_id="localhost", origin=f"http://localhost:{pa}")
        token = a.approval.create_enrollment()
        code, body = http("POST", pa, f"/passkey/enroll/{token}/options", origin=f"http://localhost:{pa}", body={})
        cid = json.loads(body)["challenge_id"]
        assert http("POST", pa, f"/passkey/enroll/{token}/verify", origin=f"http://localhost:{pa}",
                    body={"challenge_id": cid, "credential": dev.register(cid)})[0] == 200
        # draft made by instance B, approved on instance B's page with the passkey enrolled on A's page
        d = b.prepare_email("o", "Ferdinand", "S", "T")
        ob = f"http://localhost:{pb}"
        dev.origin = ob
        code, body = http("POST", pb, f"/approve/{d['draft_id']}/options", origin=ob, body={})
        cid = json.loads(body)["challenge_id"]
        assert http("POST", pb, f"/approve/{d['draft_id']}/verify", origin=ob,
                    body={"challenge_id": cid, "credential": dev.assert_(cid)})[0] == 200
        assert b.send_email("o", d["draft_id"])["status"] == "sent"
        # a page link from A does not work on B's port and the other way round
        da = a.prepare_email("o", "Ferdinand", "S2", "T2")
        assert http("GET", pb, f"/approve/{da['draft_id']}", host=f"localhost:{pa}")[0] == 421
    finally:
        ha.stop(); hb.stop()


# -- the one-time passkey link follows the page ------------------------------------------------------

def run_enroll(tmp_path, first):
    import io

    from outbox_mcp.admin_cli import main

    out = io.StringIO()
    env = {"OUTBOX_DB_PATH": str(tmp_path / "enroll.db"), "OUTBOX_APPROVAL_HOST": "localhost",
           "OUTBOX_APPROVAL_PORT": str(first)}
    code = main(["passkey-enroll"], env=env, stdout=out)
    return code, out.getvalue(), tmp_path / "enroll.db"


def test_the_enroll_link_points_to_the_page_that_answers(config, sender, tmp_path):
    first = free_block(4)
    squatters = occupy(first, 1)                                 # the configured port is taken by something else
    svc = make_service(config, sender, first)
    handle = start_local_listener(svc.approval)
    try:
        assert handle.port == first + 1
        wait_until_up(handle.port)
        # the CLI reads the same database as the running server
        import io

        from outbox_mcp.admin_cli import main

        out = io.StringIO()
        env = {"OUTBOX_DB_PATH": str(config.db_path), "OUTBOX_APPROVAL_HOST": "localhost",
               "OUTBOX_APPROVAL_PORT": str(first)}
        assert main(["passkey-enroll"], env=env, stdout=out) == 0
        text = out.getvalue()
        assert f"http://localhost:{first + 1}/passkey/enroll/" in text
        assert f"localhost:{first}/" not in text
        link = [l for l in text.splitlines() if l.startswith("http://")][0]
        path = link.split(f"localhost:{first + 1}", 1)[1]
        assert http("GET", first + 1, path)[0] == 200             # and the link really opens
        assert "No approval page answers" not in text
    finally:
        handle.stop()
        for s in squatters:
            s.close()


def test_the_enroll_link_skips_a_foreign_program_on_the_configured_port(config, sender):
    import http.server
    import io
    import threading

    from outbox_mcp.admin_cli import main

    first = free_block(4)

    class Foreign(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"not the approval page")

        def log_message(self, *a):
            pass

    foreign = http.server.HTTPServer(("127.0.0.1", first), Foreign)
    t = threading.Thread(target=foreign.serve_forever, daemon=True); t.start()
    svc = make_service(config, sender, first)
    handle = start_local_listener(svc.approval)
    try:
        assert handle.port == first + 1
        wait_until_up(handle.port)
        out = io.StringIO()
        env = {"OUTBOX_DB_PATH": str(config.db_path), "OUTBOX_APPROVAL_HOST": "localhost",
               "OUTBOX_APPROVAL_PORT": str(first)}
        assert main(["passkey-enroll"], env=env, stdout=out) == 0
        assert f"http://localhost:{first + 1}/passkey/enroll/" in out.getvalue()
    finally:
        handle.stop()
        foreign.shutdown(); foreign.server_close()


def test_the_enroll_link_says_so_when_no_page_is_running(tmp_path):
    first = free_block(2)
    code, text, _ = run_enroll(tmp_path, first)
    assert code == 0
    assert f"http://localhost:{first}/passkey/enroll/" in text   # the configured port, as before
    assert "No approval page answers right now" in text and "restart the assistant" in text


def test_the_public_setup_prints_its_link_unchanged(tmp_path):
    import io

    from outbox_mcp.admin_cli import main

    out = io.StringIO()
    env = {"OUTBOX_DB_PATH": str(tmp_path / "pub.db"), "OUTBOX_APPROVAL_HOST": "relay.example.org"}
    assert main(["passkey-enroll"], env=env, stdout=out) == 0
    assert "https://relay.example.org/passkey/enroll/" in out.getvalue()
    assert "No approval page" not in out.getvalue()
