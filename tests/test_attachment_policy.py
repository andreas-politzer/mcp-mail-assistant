import os
import threading
import unicodedata
from pathlib import Path

import pytest

from outbox_mcp.errors import AttachmentRejected, ConfigError
from outbox_mcp.security.attachments import AttachmentPolicy, safe_filename


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    for d in ("Projects", "Projects/Private", "Notes", "Banking N26", "Projects/.git", "Projects/Übersicht"):
        (v / d).mkdir(parents=True)
    (v / "Projects/note.md").write_text("# Projektnotiz\n\nHallo Ferdinand, schöne Grüße.\n", encoding="utf-8")
    (v / "Projects/second.md").write_text("zweite Datei", encoding="utf-8")
    (v / "Projects/third.md").write_text("dritte Datei", encoding="utf-8")
    (v / "Projects/fourth.md").write_text("vierte Datei", encoding="utf-8")
    (v / "Projects/Private/x.md").write_text("privat", encoding="utf-8")
    (v / "Projects/Übersicht/a.md").write_text("Übersicht", encoding="utf-8")
    (v / "Projects/.hidden.md").write_text("versteckt", encoding="utf-8")
    (v / "Projects/.git/x.md").write_text("git", encoding="utf-8")
    (v / "Notes/other.md").write_text("andere Notiz", encoding="utf-8")
    (v / "Banking N26/x.md").write_text("Konto", encoding="utf-8")
    return v


def policy(vault, **kw):
    args = dict(root=vault, allowed_folders=["Projects"], denied_folders=["Projects/Private", "Banking N26"])
    args.update(kw)
    return AttachmentPolicy(**args)


def rejected(pol, paths, match=None):
    with pytest.raises(AttachmentRejected, match=match):
        pol.load(paths)


# -- happy path ---------------------------------------------------------------------

def test_loads_a_note(vault):
    [a] = policy(vault).load(["Projects/note.md"])
    assert a.name == "note.md"
    assert a.data == (vault / "Projects/note.md").read_bytes()
    assert a.size == len(a.data)
    assert len(a.sha256) == 64


def test_order_is_kept_and_nothing_is_fine(vault):
    names = [a.name for a in policy(vault).load(["Projects/second.md", "Projects/note.md"])]
    assert names == ["second.md", "note.md"]
    assert policy(vault).load([]) == []


def test_uppercase_md_extension_is_fine(vault):
    (vault / "Projects/Big.MD").write_text("x", encoding="utf-8")
    assert policy(vault).load(["Projects/Big.MD"])[0].name == "Big.MD"


# -- folders ------------------------------------------------------------------------

def test_outside_allowed_folder(vault):
    rejected(policy(vault), ["Notes/other.md"], "not allowed")


def test_no_allowed_folders_means_nothing_is_allowed(vault):
    rejected(policy(vault, allowed_folders=[]), ["Projects/note.md"], "not allowed")


def test_denied_folder_wins_over_allowed(vault):
    rejected(policy(vault), ["Projects/Private/x.md"], "not allowed")


def test_denied_folder_is_case_insensitive(vault):
    rejected(policy(vault), ["projects/PRIVATE/x.md"], "not allowed")
    rejected(policy(vault, allowed_folders=["Banking N26"], denied_folders=["Banking N26"]),
             ["banking n26/x.md"], "not allowed")


def test_allowed_and_denied_compare_unicode_normalised(vault):
    nfd = unicodedata.normalize("NFD", "Projects/Übersicht")
    nfc = unicodedata.normalize("NFC", "Projects/Übersicht/a.md")
    rejected(policy(vault, denied_folders=[nfd]), [nfc], "not allowed")
    assert policy(vault, allowed_folders=[nfd], denied_folders=[]).load([nfc])[0].name == "a.md"


def test_folder_prefix_must_match_whole_parts(vault):
    (vault / "ProjectsExtra").mkdir()
    (vault / "ProjectsExtra/y.md").write_text("y", encoding="utf-8")
    rejected(policy(vault), ["ProjectsExtra/y.md"], "not allowed")


def test_hidden_files_and_folders(vault):
    rejected(policy(vault), ["Projects/.hidden.md"], "hidden")
    rejected(policy(vault), ["Projects/.git/x.md"], "hidden")


# -- path tricks --------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "", "   ", "/etc/passwd.md", "/abs/Projects/note.md", "../note.md", "Projects/../Notes/other.md",
    "Projects/./note.md", "Projects\\note.md", "Projects/note.md\x00.txt", "Projects//note.md", "~/note.md",
    "Projects/",
])
def test_bad_paths(vault, path):
    rejected(policy(vault), [path])


def test_paths_must_be_strings(vault):
    rejected(policy(vault), [123])
    rejected(policy(vault), "Projects/note.md", "list")


def test_symlinked_file_is_refused(vault, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("geheim außerhalb", encoding="utf-8")
    os.symlink(outside, vault / "Projects/link.md")
    os.symlink(vault / "Notes/other.md", vault / "Projects/inner-link.md")
    rejected(policy(vault), ["Projects/link.md"], "symbolic link")
    rejected(policy(vault), ["Projects/inner-link.md"], "symbolic link")


def test_symlinked_folder_is_refused(vault, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "z.md").write_text("z", encoding="utf-8")
    os.symlink(outside, vault / "Projects/linkdir")
    rejected(policy(vault), ["Projects/linkdir/z.md"], "symbolic link")


def test_folder_swapped_for_symlink_after_the_early_checks_is_still_refused(vault, monkeypatch):
    """The early symlink check can be raced; opening component by component must catch it."""
    os.symlink(vault / "Notes", vault / "Projects/linkdir")  # points inside the vault
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    rejected(policy(vault, allowed_folders=["Projects", "Notes"]), ["Projects/linkdir/other.md"], "symbolic link")


def test_file_swapped_for_symlink_after_the_early_checks_is_still_refused(vault, monkeypatch):
    os.symlink(vault / "Notes/other.md", vault / "Projects/inner.md")
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    rejected(policy(vault), ["Projects/inner.md"], "symbolic link")


def test_missing_file_and_directory(vault):
    rejected(policy(vault), ["Projects/missing.md"], "does not exist")
    (vault / "Projects/folder.md").mkdir()
    rejected(policy(vault), ["Projects/folder.md"], "regular file")


def test_missing_root(tmp_path):
    rejected(AttachmentPolicy(tmp_path / "nope", ["Projects"]), ["Projects/note.md"])


# -- file type and content ----------------------------------------------------------

@pytest.mark.parametrize("name", ["a.txt", "a.md.exe", "a.pdf", "a", "a.markdown"])
def test_only_markdown_files(vault, name):
    (vault / "Projects" / name).write_text("x", encoding="utf-8")
    rejected(policy(vault), [f"Projects/{name}"], ".md")


def test_invalid_utf8_and_nul(vault):
    (vault / "Projects/bin.md").write_bytes(b"\xff\xfe\x00bad")
    (vault / "Projects/nul.md").write_bytes(b"hello\x00world")
    (vault / "Projects/latin.md").write_bytes("Grüße".encode("latin-1"))
    rejected(policy(vault), ["Projects/bin.md"], "UTF-8")
    rejected(policy(vault), ["Projects/nul.md"], "text")
    rejected(policy(vault), ["Projects/latin.md"], "UTF-8")


@pytest.mark.parametrize("name", [
    "passwords.md", "Passwörter.md", "My Secret Notes.md", "token-list.md", "server.pem.md",
    "credentials.md", "Geheim.md", "api_key.md",
])
def test_sensitive_file_names(vault, name):
    (vault / "Projects" / name).write_text("harmlos", encoding="utf-8")
    rejected(policy(vault), [f"Projects/{name}"], "sensitive")


def test_sensitive_folder_names(vault):
    (vault / "Projects/Passwörter").mkdir()
    (vault / "Projects/Passwörter/a.md").write_text("harmlos", encoding="utf-8")
    rejected(policy(vault), ["Projects/Passwörter/a.md"], "sensitive")


@pytest.mark.parametrize("text", [
    "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
    "key AKIAIOSFODNN7EXAMPLE here",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "github_pat_11ABCDEFG0abcdefghijklmnop",
    "sk-abcdefghijklmnopqrstuvwxyz123456",
    "xoxb-123456789012-abcdefghij",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc",
    "password: Hamburg12345",
    "Passwort = Hamburg12345",
    "api_key=abcd1234efgh5678",
    "secret: 'abcd1234efgh'",
    "Passwort: abc",
    "- API-Key: x1",
    "Secret: foo",
    "**Token:** abc",
    "Passwort=hunter2",
    "Kennwort: Hamburg",
])
def test_secret_patterns_are_refused(vault, text):
    (vault / "Projects/leak.md").write_text("Notizen\n" + text + "\nEnde", encoding="utf-8")
    with pytest.raises(AttachmentRejected, match="secret") as err:
        policy(vault).load(["Projects/leak.md"])
    assert text.split()[-1].strip("*'") not in str(err.value) or len(text.split()[-1]) < 4


@pytest.mark.parametrize("text", [
    "Keine Passwörter in Chats.",
    "The token expires after one hour.",
    "Passwort: (steht im Passwortmanager)",
    "Der API-Schlüssel liegt in der .env des Dienstes, nicht hier.",
    "Passwort: eigenes Geräte-Passwort pro Anwendung, nicht das Passwort des Infomaniak-Kontos.",
    "Bei Verlust Zugang sperren (App-Passwort widerrufen). Token und Anmeldung wie beim Vault schützen.",
    "Scope `mail:send`, `token_endpoint_auth_method` nur `none`; Tokens als Hash in SQLite.",
])
def test_normal_prose_is_not_flagged(vault, text):
    (vault / "Projects/fine.md").write_text(text, encoding="utf-8")
    assert policy(vault).load(["Projects/fine.md"])[0].name == "fine.md"


# -- limits -------------------------------------------------------------------------

def test_too_many_files(vault):
    rejected(policy(vault, max_files=3),
             ["Projects/note.md", "Projects/second.md", "Projects/third.md", "Projects/fourth.md"], "(?i)at most 3")


def test_duplicates_are_refused(vault):
    rejected(policy(vault), ["Projects/note.md", "Projects/note.md"], "twice")


def test_file_too_large(vault):
    (vault / "Projects/big.md").write_text("a" * 101, encoding="utf-8")
    rejected(policy(vault, max_file_bytes=100), ["Projects/big.md"], "too large")


def test_total_too_large(vault):
    for n in ("a", "b"):
        (vault / f"Projects/{n}1.md").write_text("x" * 60, encoding="utf-8")
    rejected(policy(vault, max_file_bytes=100, max_total_bytes=100), ["Projects/a1.md", "Projects/b1.md"], "total")


def test_one_bad_file_rejects_everything(vault):
    rejected(policy(vault), ["Projects/note.md", "Notes/other.md"], "not allowed")


# -- names --------------------------------------------------------------------------

def test_safe_filename():
    assert safe_filename("Bericht: Q1 <final>.md") == "Bericht_ Q1 _final_.md"
    assert safe_filename("a\r\nb.md") == "a__b.md"
    assert safe_filename("Grüße.md") == "Grüße.md"
    assert safe_filename("x" * 300 + ".md").endswith(".md") and len(safe_filename("x" * 300 + ".md")) <= 120


def test_attachment_name_is_sanitised(vault):
    (vault / "Projects/Bericht: Q1.md").write_text("x", encoding="utf-8")
    assert policy(vault).load(["Projects/Bericht: Q1.md"])[0].name == "Bericht_ Q1.md"


def test_error_messages_do_not_contain_file_content(vault):
    (vault / "Projects/bin.md").write_bytes(b"GEHEIMER INHALT \xff")
    with pytest.raises(AttachmentRejected) as err:
        policy(vault).load(["Projects/bin.md"])
    assert "GEHEIMER" not in str(err.value)


def test_project_note_style_path_with_hyphens_and_subfolder(vault):
    (vault / "Projects/E-Mail-Versand").mkdir()
    (vault / "Projects/E-Mail-Versand/E-Mail-Versand.md").write_text("# E-Mail-Versand\n", encoding="utf-8")
    [a] = policy(vault).load(["Projects/E-Mail-Versand/E-Mail-Versand.md"])
    assert a.name == "E-Mail-Versand.md"


def test_named_pipe_is_refused_without_blocking(vault):
    os.mkfifo(vault / "Projects/pipe.md")
    outcome = []

    def attempt():
        try:
            policy(vault).load(["Projects/pipe.md"])
            outcome.append("loaded")
        except AttachmentRejected as exc:
            outcome.append(str(exc))

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "opening a named pipe blocked the check"
    assert "regular file" in outcome[0]


def test_refuses_to_run_without_the_required_os_support(vault, monkeypatch):
    monkeypatch.delattr(os, "O_NOFOLLOW")
    with pytest.raises(ConfigError, match="O_NOFOLLOW"):
        policy(vault)
