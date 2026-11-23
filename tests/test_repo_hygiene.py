"""Keeps private information out of the repository: checked on every file Git tracks."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"uv.lock", "frontend/package-lock.json", "tests/test_repo_hygiene.py"}  # this file names the patterns it looks for


def listing(*flags):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    done = subprocess.run(["git", "ls-files", *flags], cwd=ROOT, capture_output=True)
    if done.returncode != 0:
        pytest.skip("not a Git checkout")
    return done.stdout.decode()


def tracked():
    for name in listing("-z").split("\0"):
        if name and name not in SKIP and (ROOT / name).is_file():
            try:
                yield name, (ROOT / name).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # images and other binaries


def matches(pattern, flags=0):
    rx = re.compile(pattern, flags)
    return [f"{name}: {m.group(0)}" for name, text in tracked() for m in rx.finditer(text)]


def test_no_private_network_address_is_tracked():
    found = matches(r"\b(?!127\.|0\.0\.0\.0|255\.)(\d{1,3})\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
    # Version numbers such as 1.2.3.4 are not addresses; real addresses in prose or config would show here.
    found = [f for f in found if not re.search(r"(version|v)\s*$", f, re.I)]
    assert found == []


def test_no_personal_names_servers_or_home_paths_are_tracked():
    assert matches(r"andypol|georgie|thinkpad|obsidian-claude", re.I) == []
    assert matches(r"/Users/(?!x/|name/|you/)[A-Za-z][\w.-]*") == []
    assert matches(r"/home/(?!(?:user|you|youruser|name|example)\b)[a-z][\w.-]*") == []


PLACEHOLDER_TAILNETS = {"example", "dein-netz", "your-tailnet"}


def test_no_real_tailnet_name_is_tracked():
    """The product name Tailscale and addresses like mac.example.ts.net belong in the code and its tests.
    A real tailnet name (tail1a2b3c.ts.net, fancy-animal.ts.net) is a private detail: the part right before
    ".ts.net" must be one of the placeholders."""
    found = [hit for hit in matches(r"[A-Za-z0-9-]+(?=\.ts\.net)", re.I) if hit.rsplit(": ", 1)[1].lower() not in PLACEHOLDER_TAILNETS]
    assert found == []


def test_no_environment_file_with_values_is_tracked():
    out = listing().split()
    bad = [n for n in out if re.search(r"(^|/)\.env($|\.)", n) and not n.endswith(".example")]
    assert bad == []


def test_no_private_key_or_live_token_is_tracked():
    assert matches(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----\n[A-Za-z0-9+/=]{20,}") == []
    assert matches(r"ghp_(?!abcdefghijklmnop)[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-ant-[A-Za-z0-9_-]{20,}") == []


def test_windows_never_show_the_configured_tunnel_command():
    """Only the copy button may use the configured command; everything shown uses the generic form."""
    shown = [n for n, t in tracked() if n.startswith("frontend/src/") and "{TUNNEL_COMMAND}" in t]
    assert shown == []
