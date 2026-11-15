"""The admin API: sign-in and the recipient endpoints, as a small separate ASGI app.

It never talks to the mail service. The two only share the database file. The sign-in session
protects these endpoints and nothing else: sending mail through the AI connector needs no admin
session, and the rules are checked by the mail service itself on every send.

Browser-facing protections: the Host header must be on a list, a browser Origin must be on an
exact list (CORS is not authentication), the session token travels as a Bearer header and never
as a cookie, and request bodies are small.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..auth.passwords import verify_password
from ..errors import OutboxError, RecipientNotAllowed, ValidationError
from ..security.attachments import key_parts, path_key
from ..security.rate_limiter import HARD_MAX_SENDS_PER_DAY, HARD_MAX_SENDS_PER_HOUR
from ..storage.database import Database
from ..storage.recipient_store import RecipientRecord, RecipientStore
from .config import AdminConfig
from .folders import (
    apply_change, attachable_files, clean_relative, count_attachable, describe, fingerprint,
    is_ambiguous, list_folder, load_rules, real_entry,
)
from .sessions import ConfirmationStore, LoginThrottle, SessionStore

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
    confirmations = ConfirmationStore(config.confirmation_seconds, clock)
    root = config.attachment_root

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
            except Exception:  # never leak details; the database transaction has been rolled back
                logging.getLogger("outbox_mcp.admin").exception("admin request failed")
                return fail(request, 500, "internal", "Something went wrong. Nothing was changed.")

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
                "Access-Control-Allow-Methods": "GET, POST, PUT",
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
        return reply(request, 200, {
            "schema_version": db.schema_version(), "recipients": counts,
            "attachment_root": str(root) if root is not None else None,
        })

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


    # -- folders, rules and the confirmation step -------------------------------------

    def need_root() -> Path:
        if root is None or not root.is_dir():
            raise ValidationError("Attachment folders are not set up (OUTBOX_ADMIN_ATTACHMENT_ROOT).")
        return root

    async def folders(request: Request) -> Response:
        base = need_root()
        rules = await run_in_threadpool(load_rules, db, base)
        data = await run_in_threadpool(list_folder, base, request.query_params.get("path", ""), rules)
        return reply(request, 200, data)

    def plan_rule(path: str, effect: str) -> Dict[str, Any]:
        base = need_root()
        if effect not in ("allow", "deny", "clear"):
            raise ValidationError("'effect' must be allow, deny or clear.")
        rel = clean_relative(path)
        rules = load_rules(db, base)
        old = next((r.effect for r in rules if r.parts == key_parts(path_key(rel))), None)
        kind = None
        if effect != "clear":
            kind = real_entry(base, rel)  # exists, no symbolic link on the way
            if effect == "allow" and kind == "file":
                raise ValidationError("A single file can only be blocked. Allow its folder instead.")
            if effect == "allow" and is_ambiguous(base, rel):
                raise ValidationError(
                    "Two entries here differ only in case or Unicode form. Blocking is possible, allowing is not."
                )
        unchanged = (old is None) if effect == "clear" else (old == effect)
        loosening = effect == "allow" or (effect == "clear" and old == "deny")
        return {"rel": rel, "effect": effect, "old": old, "kind": kind, "rules": rules, "base": base,
                "unchanged": unchanged, "loosening": loosening}

    def preview_of(plan: Dict[str, Any]) -> Dict[str, Any]:
        try:
            files, truncated = attachable_files(plan["base"], plan["rel"])
        except OutboxError:
            files, truncated = [], False  # the folder is gone; clearing its rule gains nothing
        after = apply_change(plan["rules"], plan["rel"], plan["effect"])
        return {
            "path": plan["rel"], "change": f"{plan['old'] or 'none'} -> {plan['effect']}",
            "files_attachable_before": count_attachable(files, plan["rules"]),
            "files_attachable_after": count_attachable(files, after),
            "count_is_partial": truncated,
        }

    async def put_rule(request: Request) -> Response:
        data = await read_json(request)
        plan = await run_in_threadpool(plan_rule, text(data, "path"), text(data, "effect"))
        rel, effect = plan["rel"], plan["effect"]
        if plan["unchanged"]:
            rules = await run_in_threadpool(load_rules, db, plan["base"])
            return reply(request, 200, {"status": "unchanged", "effective": describe(rel, plan["kind"] or "folder", rules)})
        token = bearer(request) or ""
        action = f"rule:{path_key(rel)}:{effect}"
        state = fingerprint(plan["rules"])
        if plan["loosening"]:
            given = data.get("confirmation_id")
            if given is None:
                cid = confirmations.issue(token, action, state)
                preview = await run_in_threadpool(preview_of, plan)
                return reply(request, 200, {
                    "status": "confirmation_required", "confirmation_id": cid,
                    "expires_in": config.confirmation_seconds, "preview": preview,
                })
            if not confirmations.consume(given, token, action, state):
                return fail(request, 409, "confirmation_invalid",
                            "The confirmation is invalid, used up, expired, or the rules changed in the meantime. Ask again.")
        await run_in_threadpool(
            db.set_file_rule, "attach", plan["base"], rel, "cleared" if effect == "clear" else effect, clock(), SOURCE
        )
        rules = await run_in_threadpool(load_rules, db, plan["base"])
        return reply(request, 200, {"status": "done", "effective": describe(rel, plan["kind"] or "folder", rules)})

    async def unblock(request: Request) -> Response:
        rid = int(request.path_params["rid"])
        data = await read_json(request)
        record = await run_in_threadpool(store.find, rid)
        if record.status != "blocked":
            raise ValidationError("This address is not blocked.")
        token = bearer(request) or ""
        action = f"unblock:{rid}"
        state = hashlib.sha256(f"{record.status}:{record.decided_at}:{record.address}".encode()).hexdigest()
        given = data.get("confirmation_id")
        if given is None:
            cid = confirmations.issue(token, action, state)
            return reply(request, 200, {
                "status": "confirmation_required", "confirmation_id": cid,
                "expires_in": config.confirmation_seconds,
                "preview": {
                    "name": record.name, "address": record.address,
                    "effect": "The address comes off the block list. It does not become active; add or approve it again to allow mail.",
                },
            })
        if not confirmations.consume(given, token, action, state):
            return fail(request, 409, "confirmation_invalid",
                        "The confirmation is invalid, used up, expired, or the address changed in the meantime. Ask again.")
        done = await run_in_threadpool(store.unblock, rid, clock(), SOURCE)
        return reply(request, 200, {"status": "done", "recipient": _recipient(done)})

    # -- send limits -------------------------------------------------------------------

    def limits_view() -> Dict[str, Any]:
        """The values that really apply, and where they come from. `source` is 'stored' (set here),
        'mail_service_settings' (the mail service's own settings file, as that process reported it)
        or 'unknown' (nothing stored, mail service has not reported yet; the values are then null).
        The starting values are shown separately and labelled as such."""
        effective, source, reported = db.admin_limits()
        used_hour, used_day = db.send_counts(clock())
        return {
            "per_hour": effective[0] if effective else None, "per_day": effective[1] if effective else None,
            "source": source,
            "starting_values": {"per_hour": reported[0], "per_day": reported[1]} if reported else None,
            "ceiling": {"per_hour": HARD_MAX_SENDS_PER_HOUR, "per_day": HARD_MAX_SENDS_PER_DAY},
            "used": {"last_hour": used_hour, "last_day": used_day},
        }

    async def get_limits(request: Request) -> Response:
        return reply(request, 200, await run_in_threadpool(limits_view))

    async def put_limits(request: Request) -> Response:
        data = await read_json(request)
        new_hour, new_day = data.get("per_hour"), data.get("per_day")
        for value, ceiling, name in ((new_hour, HARD_MAX_SENDS_PER_HOUR, "per_hour"), (new_day, HARD_MAX_SENDS_PER_DAY, "per_day")):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= ceiling:
                raise ValidationError(f"'{name}' must be a whole number from 0 to {ceiling}.")
        current, source, _ = await run_in_threadpool(db.admin_limits)
        if source == "stored" and current == (new_hour, new_day):
            return reply(request, 200, {"status": "unchanged", **(await run_in_threadpool(limits_view))})
        # With no known current value, anything above "stopped" counts as loosening.
        loosening = (new_hour > 0 or new_day > 0) if current is None else (new_hour > current[0] or new_day > current[1])
        if loosening:
            token = bearer(request) or ""
            action = f"limits:{new_hour}:{new_day}"
            shown = ("unknown", "unknown") if current is None else (str(current[0]), str(current[1]))
            state = hashlib.sha256(f"{shown[0]}:{shown[1]}:{source}".encode()).hexdigest()
            given = data.get("confirmation_id")
            if given is None:
                cid = confirmations.issue(token, action, state)
                return reply(request, 200, {
                    "status": "confirmation_required", "confirmation_id": cid, "expires_in": config.confirmation_seconds,
                    "preview": {"per_hour": f"{shown[0]} -> {new_hour}", "per_day": f"{shown[1]} -> {new_day}"},
                })
            if not confirmations.consume(given, token, action, state):
                return fail(request, 409, "confirmation_invalid",
                            "The confirmation is invalid, used up, expired, or the limits changed in the meantime. Ask again.")
        await run_in_threadpool(db.set_send_limits, new_hour, new_day, clock(), SOURCE)
        return reply(request, 200, {"status": "done", **(await run_in_threadpool(limits_view))})

    async def audit(request: Request) -> Response:
        def number(name: str, default: Optional[int]) -> Optional[int]:
            raw = request.query_params.get(name)
            if raw is None:
                return default
            if not raw.isdigit():
                raise ValidationError(f"'{name}' must be a number.")
            return int(raw)

        limit = min(max(number("limit", 50) or 50, 1), 200)
        rows = await run_in_threadpool(db.audit_page, limit, number("before", None))
        items = [
            {"seq": r["seq"], "ts": r["ts"], "event": r["event"], "draft_id": r["draft_id"], "by": r["owner"],
             "address": r["recipient_address"], "detail": r["detail"]}
            for r in rows
        ]
        return reply(request, 200, {"entries": items, "next_before": items[-1]["seq"] if len(items) == limit else None})

    routes = [
        Route("/admin/login", endpoint(login, auth=False), methods=["POST"]),
        Route("/admin/logout", endpoint(logout), methods=["POST"]),
        Route("/admin/status", endpoint(status), methods=["GET"]),
        Route("/admin/recipients", endpoint(list_recipients), methods=["GET"]),
        Route("/admin/recipients", endpoint(add_recipient), methods=["POST"]),
    ]
    routes += [
        Route("/admin/recipients/{rid:int}/unblock", endpoint(unblock), methods=["POST"]),
        Route("/admin/folders", endpoint(folders), methods=["GET"]),
        Route("/admin/folders/rule", endpoint(put_rule), methods=["PUT"]),
        Route("/admin/audit", endpoint(audit), methods=["GET"]),
        Route("/admin/limits", endpoint(get_limits), methods=["GET"]),
        Route("/admin/limits", endpoint(put_limits), methods=["PUT"]),
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
