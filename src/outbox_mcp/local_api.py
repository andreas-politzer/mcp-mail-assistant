"""The admin API for the desktop app, without a network: `local api`.

The program reads one JSON line per request from standard input and answers with one JSON line on standard
output:

    {"method": "GET", "path": "/admin/recipients", "body": null}
    {"status": 200, "data": {...}}

It runs the same admin application the server runs (every route, every check, every confirmation step) and
calls it directly in this process. No port is opened and no sign-in is shown: the program signs in to its own
application with a passphrase it makes up for the run and never writes anywhere, and keeps the session token to
itself. The window never sees a token. When the app closes standard input, the program ends.

Only paths below /admin/ are served, and not the sign-in or sign-out routes.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any, Dict, Mapping, Optional, TextIO, Tuple
from urllib.parse import urlsplit

from .admin.app import build_admin_app
from .admin.config import AdminConfig
from .auth.passwords import hash_password
from .paths import default_file

METHODS = ("GET", "POST", "PUT")
_BLOCKED = ("/admin/login", "/admin/logout")
MAX_LINE = 24 * 1024 * 1024  # a little more than the biggest upload pass the admin application accepts


async def _call(app: Any, method: str, path: str, body: Optional[bytes], token: Optional[str]) -> Tuple[int, Any]:
    """One request, handed to the ASGI application directly."""
    parts = urlsplit(path)
    headers = [(b"host", b"localhost"), (b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if body is not None:
        headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
        "path": parts.path, "raw_path": parts.path.encode(), "query_string": parts.query.encode(),
        "headers": headers, "client": ("127.0.0.1", 0), "server": ("localhost", 80), "root_path": "",
    }
    sent = False

    async def receive() -> Dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body or b"", "more_body": False}

    status = 500
    chunks = []

    async def send(message: Dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    raw = b"".join(chunks)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return status, data


class LocalApi:
    """The admin application plus its private session."""

    def __init__(self, env: Mapping[str, str]):
        passphrase = secrets.token_urlsafe(32)
        config = AdminConfig(
            db_path=default_file(env, "outbox.db"),
            passphrase_hash=hash_password(passphrase),
            idle_seconds=10**9,
            max_session_seconds=10**9,
        )
        self._app = build_admin_app(config)
        status, data = asyncio.run(
            _call(self._app, "POST", "/admin/login", json.dumps({"passphrase": passphrase}).encode(), None)
        )
        if status != 200 or not isinstance(data.get("token"), str):
            raise RuntimeError("Die lokale Verwaltung konnte nicht gestartet werden.")
        self._token: str = data["token"]

    def handle(self, request: Any) -> Dict[str, Any]:
        if not isinstance(request, dict):
            return {"status": 400, "data": {"error": "bad_request", "message": "Unverständliche Anfrage."}}
        method, path, body = request.get("method"), request.get("path"), request.get("body")
        if (
            method not in METHODS
            or not isinstance(path, str)
            or not path.startswith("/admin/")
            or urlsplit(path).path in _BLOCKED
            or ".." in path
        ):
            return {"status": 400, "data": {"error": "bad_request", "message": "Diese Anfrage ist nicht erlaubt."}}
        raw = None if body is None else json.dumps(body).encode()
        status, data = asyncio.run(_call(self._app, method, path, raw, self._token))
        return {"status": status, "data": data}


def serve(env: Mapping[str, str], stdin: TextIO, stdout: TextIO) -> int:
    api = LocalApi(env)
    print(json.dumps({"ready": True}), file=stdout, flush=True)
    for line in stdin:
        if not line.strip():
            continue
        try:
            answer = api.handle(json.loads(line))
        except ValueError:
            answer = {"status": 400, "data": {"error": "bad_request", "message": "Unverständliche Anfrage."}}
        except Exception:  # one bad request must not end the session; the details stay out of the answer
            answer = {"status": 500, "data": {"error": "internal", "message": "Das ging schief."}}
        print(json.dumps(answer, ensure_ascii=False), file=stdout, flush=True)
    return 0
