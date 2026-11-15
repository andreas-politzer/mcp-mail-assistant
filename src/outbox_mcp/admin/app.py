"""The admin API: sign-in and the recipient endpoints, as a small separate ASGI app.

It never talks to the mail service. The two only share the database file. The sign-in session
protects these endpoints and nothing else: sending mail through the AI connector needs no admin
session, and the rules are checked by the mail service itself on every send.

Browser-facing protections: the Host header must be on a list, a browser Origin must be on an
exact list (CORS is not authentication), the session token travels as a Bearer header and never
as a cookie, and request bodies are small.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..auth.passwords import verify_password
from ..errors import OutboxError, RecipientNotAllowed, ValidationError
from ..storage.database import Database
from ..storage.recipient_store import RecipientRecord, RecipientStore
from .config import AdminConfig
from .sessions import LoginThrottle, SessionStore

SOURCE = "admin:web"
_STATUSES = ("suggested", "active", "rejected", "removed", "expired", "blocked")


def _recipient(r: RecipientRecord) -> Dict[str, Any]:
    return {
        "id": r.id, "name": r.name, "address": r.address, "aliases": list(r.aliases),
        "status": r.status, "note": r.note, "created_at": r.created_at, "decided_at": r.decided_at,
    }


def build_admin_app(config: AdminConfig, clock: Callable[[], float] = time.time) -> Starlette:
    db = Database(config.db_path)
    store = RecipientStore(db)
    sessions = SessionStore(config.idle_seconds, config.max_session_seconds, clock)
    throttle = LoginThrottle(config.max_failed_logins, config.lockout_seconds, clock)

    # -- helpers --------------------------------------------------------------------

    def host_ok(request: Request) -> bool:
        host = request.headers.get("host", "").lower()
        name = host.rsplit(":", 1)[0] if not host.endswith("]") and ":" in host else host
        return name in config.allowed_hosts or host in config.allowed_hosts

    def origin_of(request: Request) -> Optional[str]:
        return request.headers.get("origin")

    def reply(request: Request, status: int, data: Optional[Dict[str, Any]] = None) -> Response:
        response: Response = JSONResponse(data if data is not None else {}, status_code=status)
        origin = origin_of(request)
        if origin and origin in config.allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def fail(request: Request, status: int, code: str, message: str) -> Response:
        return reply(request, status, {"error": code, "message": message})

    def bearer(request: Request) -> Optional[str]:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        return token.strip() if scheme.lower() == "bearer" and token.strip() else None

    async def read_json(request: Request) -> Dict[str, Any]:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > config.max_body_bytes:
            raise _TooLarge()
        raw = await request.body()
        if len(raw) > config.max_body_bytes:
            raise _TooLarge()
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise ValidationError("The request body must be JSON.") from None
        if not isinstance(data, dict):
            raise ValidationError("The request body must be a JSON object.")
        return data

    def endpoint(fn, *, auth: bool = True):
        """Wrap a handler with the common checks: host, origin, session, error mapping."""

        async def run(request: Request) -> Response:
            if not host_ok(request):
                return fail(request, 400, "bad_host", "This host name is not allowed.")
            origin = origin_of(request)
            if origin is not None and origin not in config.allowed_origins:
                return fail(request, 403, "bad_origin", "This origin is not allowed.")
            if auth and not sessions.touch(bearer(request)):
                return fail(request, 401, "unauthorized", "Sign in first.")
            try:
                return await fn(request)
            except _TooLarge:
                return fail(request, 413, "too_large", "The request is too large.")
            except RecipientNotAllowed as exc:
                return fail(request, 409, "conflict", str(exc))
            except OutboxError as exc:
                return fail(request, 400, "invalid", str(exc))

        return run

    async def preflight(request: Request) -> Response:
        origin = origin_of(request)
        if not host_ok(request) or origin is None or origin not in config.allowed_origins:
            return Response(status_code=403)
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Vary": "Origin",
                "Access-Control-Allow-Methods": "GET, POST",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "600",
            },
        )

    def text(data: Dict[str, Any], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str):
            raise ValidationError(f"'{key}' must be text.")
        return value

    def alias_list(data: Dict[str, Any]) -> list:
        value = data.get("aliases", [])
        if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
            raise ValidationError("'aliases' must be a list of text.")
        return value

    # -- sign-in --------------------------------------------------------------------

    async def login(request: Request) -> Response:
        data = await read_json(request)
        passphrase = data.get("passphrase")
        refused = fail(request, 401, "login_failed", "Sign-in not possible.")
        if not isinstance(passphrase, str) or throttle.locked():
            return refused  # same answer for a wrong passphrase and for a lockout
        ok = await run_in_threadpool(verify_password, passphrase, config.passphrase_hash)
        now = clock()
        if not ok:
            started = throttle.failed()
            await run_in_threadpool(db.log_event, "admin_login_failed", SOURCE, None, now)
            if started:
                await run_in_threadpool(db.log_event, "admin_login_locked", SOURCE, None, now)
            return refused
        throttle.succeeded()
        token = sessions.create()
        await run_in_threadpool(db.log_event, "admin_login", SOURCE, None, now)
        return reply(request, 200, {
            "token": token, "idle_seconds": config.idle_seconds, "max_seconds": config.max_session_seconds,
        })

    async def logout(request: Request) -> Response:
        sessions.revoke(bearer(request))
        await run_in_threadpool(db.log_event, "admin_logout", SOURCE, None, clock())
        return reply(request, 200, {"status": "signed_out"})

    # -- status and recipients --------------------------------------------------------

    async def status(request: Request) -> Response:
        records = await run_in_threadpool(store.all_records)
        counts = {s: 0 for s in _STATUSES}
        for r in records:
            counts[r.status] += 1
        return reply(request, 200, {"schema_version": db.schema_version(), "recipients": counts})

    async def list_recipients(request: Request) -> Response:
        wanted = request.query_params.get("status")
        if wanted is not None and wanted not in _STATUSES:
            raise ValidationError("Unknown status.")
        records = await run_in_threadpool(store.all_records)
        return reply(request, 200, {
            "recipients": [_recipient(r) for r in records if wanted is None or r.status == wanted]
        })

    async def add_recipient(request: Request) -> Response:
        data = await read_json(request)
        done = await run_in_threadpool(
            store.add, text(data, "name"), text(data, "address"), alias_list(data), clock(), SOURCE
        )
        return reply(request, 201, {"recipient": _recipient(done)})

    def transition(action: str):
        async def handler(request: Request) -> Response:
            rid = int(request.path_params["rid"])
            if action == "approve":
                data = await read_json(request)
                name = data.get("name")
                if name is not None and not isinstance(name, str):
                    raise ValidationError("'name' must be text.")
                aliases = alias_list(data) if "aliases" in data else None
                done = await run_in_threadpool(store.approve, rid, clock(), SOURCE, name, aliases)
            else:
                done = await run_in_threadpool(getattr(store, action), rid, clock(), SOURCE)
            return reply(request, 200, {"recipient": _recipient(done)})

        return handler

    # `unblock` is deliberately not routed yet: lifting a block loosens a rule and comes with the
    # confirmation step.
    routes = [
        Route("/admin/login", endpoint(login, auth=False), methods=["POST"]),
        Route("/admin/logout", endpoint(logout), methods=["POST"]),
        Route("/admin/status", endpoint(status), methods=["GET"]),
        Route("/admin/recipients", endpoint(list_recipients), methods=["GET"]),
        Route("/admin/recipients", endpoint(add_recipient), methods=["POST"]),
    ]
    for action in ("approve", "reject", "block", "remove"):
        routes.append(Route("/admin/recipients/{rid:int}/" + action, endpoint(transition(action)), methods=["POST"]))
    routes.append(Route("/admin/{path:path}", preflight, methods=["OPTIONS"]))
    app = Starlette(routes=routes)
    app.state.sessions = sessions
    app.state.throttle = throttle
    return app


class _TooLarge(Exception):
    pass
