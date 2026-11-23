"""Phone use, step 2: Tailscale Funnel. The tailscale program and launchctl are never run: recording stand-ins
take their place, and nothing outside the temporary folders is touched."""

import io
import json
import os
import subprocess

import pytest

from outbox_mcp import remote, setup_cli, tunnel
from outbox_mcp.auth.passwords import verify_password
from outbox_mcp.setup_tasks import SetupError
from outbox_mcp.tunnel import Result
from test_remote import PASSPHRASE, Launchctl

HOST = "mein-mac.example.ts.net"
ADDRESS = f"https://{HOST}"
TS = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
ADMIN = "https://login.tailscale.com/f/funnel?node=nABC123"


def serve_config(proxy=f"http://127.0.0.1:{remote.PORT}", funnel=True, host=HOST):
    key = f"{host}:443"
    config = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {key: {"Handlers": {"/": {"Proxy": proxy}}}},
    }
    if funnel:
        config["AllowFunnel"] = {key: True}
    return config


class Tailscale:
    """The few things the program is asked, answered the way the real one does (as far as known)."""

    def __init__(self, backend="Running", dns=f"{HOST}.", config=None, needs_admin=False, funnel_fails=False,
                 funnel_json_unsupported=False):
        self.backend, self.dns, self.config = backend, dns, config if config is not None else {}
        self.needs_admin, self.funnel_fails = needs_admin, funnel_fails
        self.funnel_json_unsupported = funnel_json_unsupported
        self.calls = []

    def __call__(self, args, timeout):
        assert args[0] == TS and all(isinstance(a, str) for a in args)
        rest = args[1:]
        self.calls.append(rest)
        if rest == ["status", "--json"]:
            return Result(0, json.dumps({"BackendState": self.backend, "Self": {"DNSName": self.dns}}))
        if rest == ["funnel", "status", "--json"]:
            if self.funnel_json_unsupported:
                return Result(2, "", "unknown flag: --json")
            return Result(0, json.dumps(self.config))
        if rest == ["serve", "status", "--json"]:
            return Result(0, json.dumps(self.config))
        if rest == ["funnel", "--bg", str(remote.PORT)]:
            if self.needs_admin:
                return Result(-1, f"\nFunnel is not enabled on your tailnet.\nTo enable, visit:\n\n\t {ADMIN}\n", "", True)
            if self.funnel_fails:
                return Result(1, "", "error: something went wrong\nsecond line")
            self.config = serve_config()
            return Result(0, "Available on the internet:\n")
        raise AssertionError(f"unexpected call {rest}")

    def started_funnel(self):
        return ["funnel", "--bg", str(remote.PORT)] in self.calls


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "mailbox.json").write_text(json.dumps(
        {"email": "me@example.org", "host": "smtp.example.org", "port": 587, "security": "starttls"}))
    return {"OUTBOX_DATA_DIR": str(data), "HOME": str(tmp_path / "home")}


@pytest.fixture
def program(tmp_path):
    path = tmp_path / "Programme" / "Mail Assistant"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def good_fetch(address=ADDRESS):
    def fetch(method, url, timeout, body):
        if method == "POST" and url == f"{address}/mcp":
            return 401, b"{}"
        if method == "GET" and url == f"{address}/.well-known/oauth-authorization-server":
            return 200, json.dumps({"issuer": address + "/"}).encode()
        raise AssertionError((method, url))
    return fetch


def enable(env, program, ts, launchctl=None, **kw):
    return tunnel.enable(env, program, kw.pop("address", ADDRESS), run=ts, launchctl=launchctl or Launchctl(),
                         uid=501, tailscale=TS, fetch=kw.pop("fetch", good_fetch()), sleep=lambda s: None,
                         platform="darwin", **kw)


# --- finding the program ------------------------------------------------------------------------

def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def test_the_program_on_the_path_comes_first(tmp_path):
    on_path, app = executable(tmp_path / "bin" / "tailscale"), executable(tmp_path / "App" / "Tailscale")
    assert tunnel.find_tailscale(which=lambda name: on_path, paths=[app]) == on_path


def test_a_program_started_from_the_finder_finds_the_app_install(tmp_path):
    app = executable(tmp_path / "App" / "Tailscale")
    assert tunnel.find_tailscale(which=lambda name: None, paths=[str(tmp_path / "nein"), app]) == app


def test_the_usual_places_are_all_looked_at():
    assert "/Applications/Tailscale.app/Contents/MacOS/Tailscale" in tunnel.KNOWN_PATHS
    assert "/opt/homebrew/bin/tailscale" in tunnel.KNOWN_PATHS and "/usr/local/bin/tailscale" in tunnel.KNOWN_PATHS


def test_nothing_found_or_not_executable(tmp_path):
    plain = tmp_path / "tailscale"
    plain.write_text("x")
    plain.chmod(0o644)
    assert tunnel.find_tailscale(which=lambda name: str(plain), paths=[str(tmp_path / "gibt-es-nicht")]) is None


# --- Tailscale's state --------------------------------------------------------------------------

def test_the_trailing_dot_of_the_name_is_removed_and_the_name_lowercased():
    info = tunnel.inspect(Tailscale(dns="Mein-Mac.Example.ts.net."), TS)
    assert info == {"state": "ready", "address": ADDRESS}
    assert tunnel.inspect(Tailscale(dns=HOST), TS)["address"] == ADDRESS  # also without the dot


@pytest.mark.parametrize("backend, state", [
    ("Stopped", "stopped"), ("NeedsLogin", "needs_login"), ("NoState", "needs_login"),
    ("NeedsMachineAuth", "needs_approval"), ("Starting", "not_running"), ("", "not_running"),
])
def test_tailscale_that_is_not_running_is_told_apart(backend, state):
    assert tunnel.inspect(Tailscale(backend=backend), TS) == {"state": state, "address": None}


@pytest.mark.parametrize("dns", ["", None, ".", "mac.local.", "mac.example.org."])
def test_without_a_ts_net_name_magicdns_is_missing(dns):
    assert tunnel.inspect(Tailscale(dns=dns), TS) == {"state": "no_magicdns", "address": None}


@pytest.mark.parametrize("said", [
    "failed to connect to local tailscaled; it doesn't appear to be running",
    "Failed to connect to local Tailscale service; is Tailscale running?",
    "dial unix /var/run/tailscaled.socket: connect: no such file or directory",
    "connection refused",
])
def test_a_missing_background_service_is_told_as_not_running(said):
    assert tunnel.inspect(lambda a, t: Result(1, "", said), TS) == {"state": "not_running", "address": None}


GUI_ERROR = "The Tailscale GUI failed to start: The operation couldn\u2019t be completed. (Tailscale.CLIError error 3.)"


@pytest.mark.parametrize("answer", [
    Result(1, GUI_ERROR, ""), Result(1, "", GUI_ERROR), Result(0, "[1]"), Result(0, ""), Result(0, "<html>"),
])
def test_an_answer_that_is_not_understood_is_never_called_not_running(answer):
    info = tunnel.inspect(lambda a, t: answer, TS)
    assert info["state"] == "unreadable" and info["address"] is None


def test_the_unreadable_state_names_a_short_cause_and_says_so_in_german():
    answer = tunnel.status(run=lambda a, t: Result(1, "", GUI_ERROR), tailscale=TS, platform="darwin")
    assert answer["state"] == "unreadable" and answer["address"] is None
    assert answer["reason"].startswith("Tailscale konnte nicht abgefragt werden. Details: The Tailscale GUI failed to start")
    assert "läuft nicht" not in answer["reason"]
    assert len(answer["reason"]) < 260


def test_a_very_long_cause_is_cut():
    answer = tunnel.status(run=lambda a, t: Result(1, "", "x" * 1000), tailscale=TS, platform="darwin")
    assert answer["state"] == "unreadable" and len(answer["reason"]) < 260


def test_no_answer_in_time_is_unreadable_not_not_running():
    info = tunnel.inspect(lambda a, t: Result(-1, "", "connection refused", True), TS)
    assert info["state"] == "unreadable" and "10 Sekunden" in info["details"]


def test_the_step_refuses_an_unreadable_tailscale_with_the_cause(env, program):
    lc = Launchctl()
    with pytest.raises(SetupError, match=r"nicht abgefragt werden\. Details: The Tailscale GUI"):
        enable(env, program, lambda a, t: Result(1, "", GUI_ERROR), lc)
    assert lc.calls == []


# --- the environment of the tailscale program ---------------------------------------------------

def test_a_program_without_a_terminal_still_gets_one_for_tailscale():
    assert tunnel._cli_env({"HOME": "/Users/you/home"})["TERM"] == "dumb"
    assert tunnel._cli_env({"TERM": ""})["TERM"] == "dumb"
    assert tunnel._cli_env({"TERM": "  "})["TERM"] == "dumb"


def test_a_terminal_that_is_there_is_kept_and_nothing_else_is_touched():
    base = {"TERM": "xterm-256color", "HOME": "/Users/you/home", "PATH": "/usr/bin"}
    assert tunnel._cli_env(base) == base
    assert tunnel._cli_env(base) is not base


def test_the_real_call_passes_that_environment_and_no_input(monkeypatch):
    seen = {}

    def fake(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "{}", "")

    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setattr(tunnel.subprocess, "run", fake)
    assert tunnel._run([TS, "status", "--json"], 5.0) == Result(0, "{}", "")
    assert seen["env"]["TERM"] == "dumb" and seen["stdin"] == subprocess.DEVNULL and seen["timeout"] == 5.0
    assert "TERM" not in os.environ  # only the child got it


# --- the Funnel/Serve configuration -------------------------------------------------------------

@pytest.mark.parametrize("config, expected", [
    ({}, "off"),
    (serve_config(), "ours"),
    (serve_config(funnel=False), "ours_private"),
    (serve_config(proxy="http://localhost:8430/"), "ours"),
    (serve_config(proxy="127.0.0.1:8430"), "ours"),
    (serve_config(proxy="http://127.0.0.1:3000"), "other"),
    (serve_config(proxy="http://127.0.0.1:84300"), "other"),
    (serve_config(host="anderer.example.ts.net"), "other"),
    ({"TCP": {"443": {"TCPForward": "127.0.0.1:22"}}}, "other"),
    ({"TCP": {"443": {"HTTPS": True}}}, "other"),
    ({"TCP": {"8443": {"HTTPS": True}}}, "off"),
    ({**serve_config(), "Foreground": {"abc": {"TCP": {}}}}, "other"),
    ({"TCP": {"443": {"HTTPS": True}}, "Web": {f"{HOST}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8430"}, "/x": {"Text": "hi"}}}}}, "other"),
    ({"TCP": {"443": {"HTTPS": True}}, "Web": {f"{HOST}:443": {"Handlers": {"/": {"Path": "/tmp"}}}}}, "other"),
])
def test_what_the_configuration_means(config, expected):
    assert tunnel.classify(config, HOST) == expected


@pytest.mark.parametrize("config", [None, [], "text", {"TCP": 5, "Web": {}}, {"Web": 5}])
def test_a_configuration_that_is_not_understood_is_unknown(config):
    assert tunnel.classify(config, HOST) == "unknown"


def test_older_tailscale_without_json_on_funnel_status_is_asked_via_serve():
    ts = Tailscale(config=serve_config(), funnel_json_unsupported=True)
    assert tunnel.funnel_state(ts, TS, HOST) == "ours"
    assert ts.calls == [["funnel", "status", "--json"], ["serve", "status", "--json"]]


def test_no_readable_answer_at_all_is_unknown():
    assert tunnel.funnel_state(lambda a, t: Result(1, "", "nope"), TS, HOST) == "unknown"
    assert tunnel.funnel_state(lambda a, t: Result(0, "not json"), TS, HOST) == "unknown"


# --- status: what the card shows before anything happens ---------------------------------------

def test_status_on_other_systems():
    answer = tunnel.status(run=Tailscale(), tailscale=TS, platform="linux")
    assert answer["supported"] is False and answer["state"] == "unsupported"


def test_status_without_tailscale(monkeypatch):
    monkeypatch.setattr(tunnel, "find_tailscale", lambda: None)
    answer = tunnel.status(run=Tailscale(), platform="darwin")
    assert answer["state"] == "missing" and answer["address"] is None and "tailscale.com/download" in answer["reason"]


def test_status_ready_shows_the_address_and_changes_nothing():
    ts = Tailscale()
    answer = tunnel.status(run=ts, tailscale=TS, platform="darwin")
    assert answer == {"supported": True, "state": "ready", "address": ADDRESS, "funnel": "off", "reason": None}
    assert not ts.started_funnel()
    assert all(call[-1] == "--json" and call[0] in ("status", "funnel", "serve") for call in ts.calls)


@pytest.mark.parametrize("backend", ["Stopped", "NeedsLogin", "NeedsMachineAuth", "Starting"])
def test_status_names_what_is_wrong_in_german(backend):
    answer = tunnel.status(run=Tailscale(backend=backend), tailscale=TS, platform="darwin")
    assert answer["state"] != "ready" and answer["address"] is None and answer["reason"]


def test_status_with_somebody_elses_rule_says_so_and_withholds_the_address():
    answer = tunnel.status(run=Tailscale(config=serve_config(proxy="http://127.0.0.1:3000")), tailscale=TS,
                           platform="darwin")
    assert answer["state"] == "funnel_busy" and answer["address"] is None and "überschreibt" in answer["reason"]


def test_status_with_an_unreadable_configuration_is_careful():
    answer = tunnel.status(run=lambda a, t: Result(0, json.dumps({"BackendState": "Running", "Self": {"DNSName": HOST}}))
                           if a[1] == "status" else Result(1), tailscale=TS, platform="darwin")
    assert answer["state"] == "funnel_unknown"


def test_status_with_our_own_rule_is_ready():
    answer = tunnel.status(run=Tailscale(config=serve_config()), tailscale=TS, platform="darwin")
    assert answer["state"] == "ready" and answer["funnel"] == "ours"


# --- switching the Funnel on --------------------------------------------------------------------

def test_the_funnel_command_is_the_background_form_for_our_port_only():
    ts = Tailscale()
    tunnel.start_funnel(ts, TS)
    assert ts.calls == [["funnel", "--bg", "8430"]]  # no reset, no other port, no shell


def test_a_funnel_that_is_not_allowed_yet_gives_the_link_and_the_way_on():
    with pytest.raises(tunnel.NeedsAdmin) as caught:
        tunnel.start_funnel(Tailscale(needs_admin=True), TS)
    assert caught.value.url == ADMIN
    assert "Erneut prüfen" in str(caught.value)


def test_the_link_is_found_in_the_error_output_too():
    ts = lambda a, t: Result(1, "", f"visit {ADMIN}.")
    with pytest.raises(tunnel.NeedsAdmin) as caught:
        tunnel.start_funnel(ts, TS)
    assert caught.value.url == ADMIN


def test_other_failures_are_german_with_a_short_english_detail():
    with pytest.raises(SetupError, match=r"nicht einschalten\. Details: error: something went wrong second line"):
        tunnel.start_funnel(Tailscale(funnel_fails=True), TS)
    with pytest.raises(SetupError, match="nicht rechtzeitig"):
        tunnel.start_funnel(lambda a, t: Result(-1, "", "", True), TS)


# --- looking at the result ----------------------------------------------------------------------

def test_the_address_is_accepted_when_it_answers_like_the_server():
    tunnel.check_address(ADDRESS, fetch=good_fetch(), sleep=lambda s: None)


def test_the_first_certificate_takes_time_so_a_miss_is_tried_again():
    misses = iter([OSError("tls"), (502, b"Bad Gateway"), (502, b"")])
    waits, good = [], good_fetch()

    def fetch(method, url, timeout, body):
        if method == "POST":
            miss = next(misses, None)
            if isinstance(miss, OSError):
                raise miss
            if miss is not None:
                return miss
        return good(method, url, timeout, body)

    tunnel.check_address(ADDRESS, fetch=fetch, sleep=waits.append)
    assert len(waits) == 3 and all(w == 2.5 for w in waits)  # three misses, about 8 seconds


def test_giving_up_takes_about_twenty_seconds_and_says_what_to_do():
    waits, calls = [], []

    def fetch(method, url, timeout, body):
        calls.append(method)
        raise OSError("refused")

    with pytest.raises(SetupError, match="noch nicht erreichbar.*Erneut prüfen"):
        tunnel.check_address(ADDRESS, fetch=fetch, sleep=waits.append)
    assert len(calls) == 8 and len(waits) == 7 and sum(waits) <= 20


def test_a_wrong_answer_is_not_waited_for():
    waits = []

    def fetch(method, url, timeout, body):
        return (401, b"") if method == "POST" else (200, json.dumps({"issuer": "https://anders.example.org"}).encode())

    with pytest.raises(SetupError, match="nicht mit den Angaben"):
        tunnel.check_address(ADDRESS, fetch=fetch, sleep=waits.append)
    assert waits == []


@pytest.mark.parametrize("post, get", [
    ((200, b"{}"), None), ((404, b""), None), ((401, b""), (404, b"")), ((401, b""), (200, b"kein json")),
])
def test_anything_but_401_and_the_right_description_is_not_enough(post, get):
    def fetch(method, url, timeout, body):
        return post if method == "POST" else get

    with pytest.raises(SetupError):
        tunnel.check_address(ADDRESS, fetch=fetch, sleep=lambda s: None, attempts=2)


def test_the_real_fetcher_does_not_follow_redirects():
    request = tunnel._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example.org")
    assert request is None


# --- the whole step -----------------------------------------------------------------------------

def test_happy_path_in_order(env, program):
    order = []

    class Recording(Tailscale):
        def __call__(self, args, timeout):
            order.append(("tailscale", tuple(args[1:])))
            return super().__call__(args, timeout)

    class RecordingLaunchctl(Launchctl):
        def __call__(self, args):
            order.append(("launchctl", args[1]))
            return super().__call__(args)

    ts = Recording()
    answer = tunnel.enable(env, program, ADDRESS, run=ts, launchctl=RecordingLaunchctl(), uid=501, tailscale=TS,
                           fetch=lambda m, u, t, b: (order.append(("check", u)), good_fetch()(m, u, t, b))[1],
                           sleep=lambda s: None, platform="darwin")
    assert answer == {"address": ADDRESS, "passphrase_placeholder": True}
    service = next(i for i, o in enumerate(order) if o == ("launchctl", "bootstrap"))
    funnel = next(i for i, o in enumerate(order) if o == ("tailscale", ("funnel", "--bg", "8430")))
    check = next(i for i, o in enumerate(order) if o[0] == "check")
    assert service < funnel < check
    data = json.load(open(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json")))
    assert data["public_base_url"] == ADDRESS and data["passphrase_placeholder"] is True


def test_a_confirmed_address_that_is_not_the_real_one_is_refused_before_anything_changes(env, program):
    ts, lc = Tailscale(), Launchctl()
    with pytest.raises(SetupError, match="Adresse hat sich geändert"):
        enable(env, program, ts, lc, address="https://anderer-mac.example.ts.net")
    assert lc.calls == [] and not ts.started_funnel()
    assert not os.path.exists(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json"))


def test_somebody_elses_rule_is_never_overwritten_and_nothing_is_started(env, program):
    ts, lc = Tailscale(config=serve_config(proxy="http://127.0.0.1:3000")), Launchctl()
    with pytest.raises(SetupError, match="überschreibt"):
        enable(env, program, ts, lc)
    assert lc.calls == [] and not ts.started_funnel()


def test_an_unreadable_configuration_changes_nothing(env, program):
    lc = Launchctl()
    broken = lambda a, t: Result(0, json.dumps({"BackendState": "Running", "Self": {"DNSName": HOST}})) if a[1] == "status" else Result(1)
    with pytest.raises(SetupError, match="nichts verändert"):
        enable(env, program, broken, lc)
    assert lc.calls == []


@pytest.mark.parametrize("backend", ["Stopped", "NeedsLogin", "Starting"])
def test_tailscale_that_is_not_ready_changes_nothing(env, program, backend):
    lc = Launchctl()
    with pytest.raises(SetupError):
        enable(env, program, Tailscale(backend=backend), lc)
    assert lc.calls == []


def test_without_tailscale_nothing_happens(env, program, monkeypatch):
    monkeypatch.setattr(tunnel, "find_tailscale", lambda: None)
    lc = Launchctl()
    with pytest.raises(SetupError, match="nicht installiert"):
        tunnel.enable(env, program, ADDRESS, run=Tailscale(), launchctl=lc, platform="darwin")
    assert lc.calls == []


def test_a_funnel_that_must_be_allowed_first_leaves_the_service_running_and_the_way_open(env, program):
    ts, lc = Tailscale(needs_admin=True), Launchctl()
    with pytest.raises(tunnel.NeedsAdmin) as caught:
        enable(env, program, ts, lc)
    assert caught.value.url == ADMIN
    assert "bootstrap" in lc.verbs()  # the service is up, only the Funnel is missing
    ts.needs_admin = False  # the person has switched it on in the browser: "Erneut prüfen"
    assert enable(env, program, ts, Launchctl())["address"] == ADDRESS and ts.config == serve_config()


def test_asking_again_leaves_what_is_in_place_alone(env, program):
    ts, lc = Tailscale(), Launchctl()
    enable(env, program, ts, lc)
    lc.calls.clear()
    ts.calls.clear()
    assert enable(env, program, ts, lc)["address"] == ADDRESS
    assert "bootstrap" not in lc.verbs() and "bootout" not in lc.verbs()  # service not restarted
    assert not ts.started_funnel()  # funnel not touched
    assert [c for c in ts.calls if c[0] == "funnel" and c[1] != "status"] == []


def test_our_own_private_serve_rule_just_gets_the_funnel(env, program):
    ts = Tailscale(config=serve_config(funnel=False))
    enable(env, program, ts)
    assert ts.started_funnel() and ts.config == serve_config()


def test_a_chosen_passphrase_survives_the_tunnel_step(env, program):
    remote.enable(env, program, ADDRESS, PASSPHRASE, run=Launchctl(), uid=501, platform="darwin")
    answer = enable(env, program, Tailscale())
    assert answer["passphrase_placeholder"] is False
    data = json.load(open(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json")))
    assert verify_password(PASSPHRASE, data["password_hash"])


def test_a_failed_check_is_reported_and_the_funnel_stays_for_the_next_try(env, program):
    ts = Tailscale()

    def fetch(method, url, timeout, body):
        raise OSError("not yet")

    with pytest.raises(SetupError, match="noch nicht erreichbar"):
        enable(env, program, ts, fetch=fetch)
    assert ts.config == serve_config()


def test_a_funnel_that_does_not_show_up_as_on_is_not_good_enough(env, program):
    class Liar(Tailscale):
        def __call__(self, args, timeout):
            done = super().__call__(args, timeout)
            if args[1:] == ["funnel", "--bg", "8430"]:
                self.config = serve_config(funnel=False)
            return done

    with pytest.raises(SetupError, match="nicht als eingeschaltet"):
        enable(env, program, Liar())


def test_other_systems_are_told_plainly(env, program):
    with pytest.raises(SetupError, match="nur für den Mac"):
        tunnel.enable(env, program, ADDRESS, run=Tailscale(), platform="linux")


# --- the command line ---------------------------------------------------------------------------

def test_cli_tunnel_status_prints_one_line_of_json(env, monkeypatch):
    monkeypatch.setattr(tunnel, "status", lambda: {"supported": True, "state": "ready", "address": ADDRESS,
                                                   "funnel": "off", "reason": None})
    out = io.StringIO()
    assert setup_cli.main(["tunnel-status"], env=env, stdout=out) == 0
    answer = json.loads(out.getvalue())
    assert answer["ok"] is True and answer["address"] == ADDRESS and out.getvalue().count("\n") == 1


def test_cli_tunnel_enable_passes_the_confirmed_address(env, program, monkeypatch):
    seen = {}
    monkeypatch.setattr(tunnel, "enable", lambda e, command, address: seen.update(command=command, address=address)
                        or {"address": address, "passphrase_placeholder": True})
    out = io.StringIO()
    assert setup_cli.main(["tunnel-enable", "--command", program, "--url", ADDRESS], env=env, stdout=out) == 0
    assert seen == {"command": program, "address": ADDRESS}
    assert json.loads(out.getvalue()) == {"ok": True, "address": ADDRESS, "passphrase_placeholder": True}


def test_cli_hands_the_admin_link_to_the_window(env, program, monkeypatch):
    def refuse(e, command, address):
        raise tunnel.NeedsAdmin("Funnel ist noch nicht freigeschaltet.", ADMIN)

    monkeypatch.setattr(tunnel, "enable", refuse)
    out = io.StringIO()
    assert setup_cli.main(["tunnel-enable", "--command", program, "--url", ADDRESS], env=env, stdout=out) == 0
    assert json.loads(out.getvalue()) == {"ok": False, "reason": "Funnel ist noch nicht freigeschaltet.", "url": ADMIN}


def test_cli_reports_other_problems_without_a_link(env, program, monkeypatch):
    def refuse(e, command, address):
        raise SetupError("Tailscale ist ausgeschaltet.")

    monkeypatch.setattr(tunnel, "enable", refuse)
    out = io.StringIO()
    setup_cli.main(["tunnel-enable", "--command", program, "--url", ADDRESS], env=env, stdout=out)
    assert json.loads(out.getvalue()) == {"ok": False, "reason": "Tailscale ist ausgeschaltet."}
