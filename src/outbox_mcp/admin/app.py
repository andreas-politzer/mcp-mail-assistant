"""The admin API: sign-in and the recipient endpoints, as a small separate ASGI app.

It never talks to the mail service. The two only share the database file. The sign-in session
protects these endpoints and nothing else: sending mail through the AI connector needs no admin
session, and the rules are checked by the mail service itself on every send.

Browser-facing protections: the Host header must be on a list, a browser Origin must be on an
exact list (CORS is not authentication), the session token travels as a Bearer header and never
as a cookie, and request bodies are small.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..auth.passwords import verify_password
from .. import sources as src_rules
from ..errors import OutboxError, RecipientNotAllowed, SourceRejected, ValidationError
from ..security.attachments import is_sensitive_name, key_parts, path_key
from ..security.rate_limiter import HARD_MAX_SENDS_PER_DAY, HARD_MAX_SENDS_PER_HOUR
from ..storage.database import Database
from ..storage.recipient_store import RecipientRecord, RecipientStore
from .config import AdminConfig
from .folders import (
    apply_change, attachable_files, clean_relative, count_attachable, describe, fingerprint,
    is_ambiguous, list_folder, load_rules, real_entry,
)
from .folders import Rule
from .folders import evaluate as rule_evaluate
from .sessions import ConfirmationStore, LoginThrottle, SessionStore
from .sync import PASS_BYTES, PASS_FILES, make_plan

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

    async def read_json(request: Request, limit: Optional[int] = None) -> Dict[str, Any]:
        limit = limit or config.max_body_bytes
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            raise _TooLarge()
        raw = await request.body()
        if len(raw) > limit:
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
            except SourceRejected as exc:
                if exc.category == "state":
                    return fail(request, 404, "not_found", str(exc))
                if exc.category in ("quota", "conflict"):
                    return fail(request, 409, exc.category, str(exc))
                return fail(request, 400, "invalid", str(exc))
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
        return await put_notes_rule(request, await read_json(request))

    async def put_notes_rule(request: Request, data: Dict[str, Any]) -> Response:
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

    def parse_since(raw: Optional[str]) -> Optional[float]:
        """Seconds since 1970, or an ISO 8601 date or time (without a zone it means UTC)."""
        if raw is None or not raw.strip():
            return None
        raw = raw.strip()
        try:
            if raw.replace(".", "", 1).isdigit():
                return float(raw)
            moment = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError("'since' must be a number of seconds or an ISO 8601 date.") from None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        return moment.timestamp()

    async def audit(request: Request) -> Response:
        def number(name: str, default: Optional[int]) -> Optional[int]:
            raw = request.query_params.get(name)
            if raw is None:
                return default
            if not raw.isdigit():
                raise ValidationError(f"'{name}' must be a number.")
            return int(raw)

        limit = min(max(number("limit", 50) or 50, 1), 200)
        types = [t.strip() for raw in request.query_params.getlist("type") for t in raw.split(",") if t.strip()]
        since = parse_since(request.query_params.get("since"))
        rows = await run_in_threadpool(db.audit_page, limit, number("before", None), types or None, since)
        items = [
            {"seq": r["seq"], "ts": r["ts"], "event": r["event"], "draft_id": r["draft_id"], "by": r["owner"],
             "address": r["recipient_address"], "detail": r["detail"]}
            for r in rows
        ]
        return reply(request, 200, {"entries": items, "next_before": items[-1]["seq"] if len(items) == limit else None})

    # -- file sources ------------------------------------------------------------------

    def get_source(sid: str):
        row = db.get_source(sid)
        if row is None or row["status"] != "active":
            raise SourceRejected("state", "This source does not exist.")
        return row

    def source_view(row, usage: Dict[str, Any]) -> Dict[str, Any]:
        per = usage["per_source"].get(row["id"], {})
        return {
            "id": row["id"], "label": row["label"], "kind": row["kind"], "scope": row["scope"],
            "origin_display": row["origin_display"], "file_name": row["file_display"],
            "created_at": row["created_at"], "epoch": row["sync_epoch"],
            "files": per.get("current", 0), "bytes": per.get("bytes", 0),
        }

    def quota_view(usage: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "sources": usage["sources"], "max_sources": src_rules.MAX_ACTIVE_SOURCES,
            "max_files_per_source": src_rules.MAX_FILES_PER_SOURCE, "max_file_bytes": src_rules.MAX_FILE_BYTES,
            "bytes_used": usage["bytes"], "max_total_bytes": src_rules.MAX_TOTAL_BYTES,
            "apply_files": PASS_FILES, "apply_bytes": PASS_BYTES,
        }

    async def list_sources_ep(request: Request) -> Response:
        def work():
            usage = db.source_usage()
            return {"sources": [source_view(r, usage) for r in db.list_sources()], "quota": quota_view(usage)}
        return reply(request, 200, await run_in_threadpool(work))

    async def create_source_ep(request: Request) -> Response:
        data = await read_json(request)
        if data.get("kind", "upload") != "upload":
            raise ValidationError("Only an upload source can be created here.")
        scope = data.get("scope", "folder")
        origin = data.get("origin_display", "")
        if not isinstance(origin, str) or len(origin) > 200 or any(ord(c) < 32 for c in origin):
            raise ValidationError("'origin_display' must be short plain text.")
        file_name = data.get("file_name") if scope == "file" else None
        sid = await run_in_threadpool(
            lambda: db.create_source(
                "upload", scope if isinstance(scope, str) else "", text(data, "label"), clock(), SOURCE,
                origin_display=origin.strip(), file_name=file_name,
            )
        )
        usage = await run_in_threadpool(db.source_usage)
        return reply(request, 201, {"source": source_view(db.get_source(sid), usage), "quota": quota_view(usage)})

    async def rename_source_ep(request: Request) -> Response:
        sid = request.path_params["sid"]
        data = await read_json(request)
        await run_in_threadpool(db.rename_source, sid, text(data, "label"), clock(), SOURCE)
        usage = await run_in_threadpool(db.source_usage)
        return reply(request, 200, {"source": source_view(db.get_source(sid), usage)})

    async def remove_source_ep(request: Request) -> Response:
        sid = request.path_params["sid"]
        effects = await run_in_threadpool(db.remove_source, sid, clock(), SOURCE)
        return reply(request, 200, {"status": "done", **effects})

    def rule_objects(sid: str) -> list:
        return [Rule(key_parts(k), e, d) for k, e, d in db.source_rules(sid)]

    async def evaluate_ep(request: Request) -> Response:
        src = await run_in_threadpool(get_source, request.path_params["sid"])
        data = await read_json(request)
        paths = data.get("paths")
        if not isinstance(paths, list) or len(paths) > 1000 or not all(isinstance(p, str) for p in paths):
            raise ValidationError("'paths' must be a list of at most 1000 paths.")

        def work():
            rules = rule_objects(src["id"])
            allowed, denied = db.source_rule_lists(src["id"])
            out = []
            for raw in paths:
                try:
                    display, key = src_rules.clean_relative_path(raw)
                except SourceRejected as exc:
                    out.append({"path": raw[:200], "state": "invalid", "error": str(exc)})
                    continue
                state = src_rules.evaluate(src["scope"], src["file_key"], allowed, denied, key)
                _, rule = rule_evaluate(key_parts(key), rules)
                parts = key_parts(key)
                out.append({
                    "path": display, "state": state,
                    "source_rule": rule.display if rule else None,
                    "inherited": bool(rule and rule.parts != parts),
                    "own_rule": next((r.effect for r in rules if r.parts == parts), None),
                    "sensitive": any(is_sensitive_name(p) for p in display.split("/")),
                })
            return out
        return reply(request, 200, {"results": await run_in_threadpool(work)})

    def stored_preview(src, rules_before: list, rules_after: list) -> Dict[str, Any]:
        def lists(rules):
            return ([r.parts for r in rules if r.effect == "allow"], [r.parts for r in rules if r.effect == "deny"])
        files = db.list_stored_files(src["id"], "current")
        count = lambda rules: sum(  # noqa: E731
            1 for f in files
            if src_rules.evaluate(src["scope"], src["file_key"], *lists(rules), f["path_key"]) == "allowed"
        )
        return {"stored_files_allowed_before": count(rules_before), "stored_files_allowed_after": count(rules_after)}

    async def source_rule_ep(request: Request) -> Response:
        sid = request.path_params["sid"]
        data = await read_json(request)
        src = await run_in_threadpool(get_source, sid)
        if src["kind"] == "server_folder":
            if root is None or src["real_path"] != db._norm_real_path(root):
                raise ValidationError("This folder source is not managed here.")
            return await put_notes_rule(request, data)
        path, effect = text(data, "path"), text(data, "effect")
        if effect not in ("allow", "deny", "clear"):
            raise ValidationError("'effect' must be allow, deny or clear.")
        try:
            display, key = db._rule_path(path)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        rules = await run_in_threadpool(rule_objects, sid)
        old = next((r.effect for r in rules if r.parts == key_parts(key)), None)
        if (old is None) if effect == "clear" else (old == effect):
            return reply(request, 200, {"status": "unchanged"})
        loosening = effect == "allow" or (effect == "clear" and old == "deny")
        if loosening:
            token = bearer(request) or ""
            action = f"source-rule:{sid}:{key}:{effect}"
            state = fingerprint(rules) + f":{src['sync_epoch']}"
            given = data.get("confirmation_id")
            if given is None:
                cid = confirmations.issue(token, action, state)
                after = apply_change(rules, display, effect)
                preview = {"path": display, "change": f"{old or 'none'} -> {effect}"}
                preview.update(await run_in_threadpool(stored_preview, src, rules, after))
                return reply(request, 200, {
                    "status": "confirmation_required", "confirmation_id": cid,
                    "expires_in": config.confirmation_seconds, "preview": preview,
                })
            if not confirmations.consume(given, token, action, state):
                return fail(request, 409, "confirmation_invalid",
                            "The confirmation is invalid, used up, expired, or the rules changed in the meantime. Ask again.")
        effects = await run_in_threadpool(
            db.set_source_rule, sid, display, "cleared" if effect == "clear" else effect, clock(), SOURCE
        )
        return reply(request, 200, {"status": "done", **effects})

    async def source_folders_ep(request: Request) -> Response:
        src = await run_in_threadpool(get_source, request.path_params["sid"])
        if src["kind"] != "server_folder" or root is None or src["real_path"] != db._norm_real_path(root):
            raise ValidationError("Only the server's own folder source has a folder tree.")
        rules = await run_in_threadpool(rule_objects, src["id"])
        data = await run_in_threadpool(list_folder, root, request.query_params.get("path", ""), rules)
        return reply(request, 200, data)

    FILES_PAGE = 100

    async def source_files_ep(request: Request) -> Response:
        src = await run_in_threadpool(get_source, request.path_params["sid"])
        if src["kind"] != "upload":
            raise ValidationError("Only an upload source lists stored files.")
        prefix = request.query_params.get("path", "")
        parts = ()
        if prefix:
            _, pkey = src_rules.clean_relative_path(prefix)
            parts = key_parts(pkey)
        cursor = request.query_params.get("cursor", "")

        def work():
            allowed, denied = db.source_rule_lists(src["id"])
            rows = [
                r for r in db.list_stored_files(src["id"])
                if key_parts(r["path_key"])[: len(parts)] == parts and r["path_key"] > cursor
            ]
            page = rows[:FILES_PAGE]
            return {
                "files": [{
                    "path": r["path_display"], "state": r["state"], "reason": r["purged_reason"],
                    "version": r["version"], "id": src_rules.short_version(r["sha256"]) if r["sha256"] else None,
                    "size": r["size_bytes"], "uploaded_at": r["uploaded_at"],
                    "allowed_now": r["state"] == "current"
                    and src_rules.evaluate(src["scope"], src["file_key"], allowed, denied, r["path_key"]) == "allowed",
                } for r in page],
                "next_cursor": page[-1]["path_key"] if len(rows) > FILES_PAGE else None,
            }
        return reply(request, 200, await run_in_threadpool(work))

    SYNC_TTL = 900

    # A finished pass is remembered for a while, so the same request can safely be repeated when the
    # answer got lost on the way (network drop after the server had already stored everything). Only the
    # same session, the same source and exactly the same files get the remembered answer; nothing is
    # applied a second time and no second audit entry is written.
    replays: Dict[str, tuple] = {}
    replay_lock = threading.Lock()
    REPLAY_MAX = 50

    def request_digest(sid: str, files: list) -> str:
        items = sorted(
            (str(f.get("path", "")), str(f.get("sha256", "")).lower(),
             hashlib.sha256(str(f.get("content", "")).encode("utf-8")).hexdigest())
            for f in files
        )
        return hashlib.sha256(json.dumps([sid, items]).encode("utf-8")).hexdigest()

    def remember(cid: str, token: str, digest: str, response: Dict[str, Any]) -> None:
        now = clock()
        with replay_lock:
            for k in [k for k, v in replays.items() if v[3] <= now]:
                del replays[k]
            while len(replays) >= REPLAY_MAX:
                del replays[min(replays, key=lambda k: replays[k][3])]
            replays[cid] = (hashlib.sha256(token.encode()).hexdigest(), digest, response, now + SYNC_TTL)

    def remembered(cid: Any, token: str, digest: str) -> Optional[Dict[str, Any]]:
        if not isinstance(cid, str):
            return None
        with replay_lock:
            item = replays.get(cid)
        if item is None or item[3] <= clock():
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        return item[2] if item[0] == key and item[1] == digest else None

    async def sync_plan_ep(request: Request) -> Response:
        sid = request.path_params["sid"]
        src = await run_in_threadpool(get_source, sid)
        data = await read_json(request, config.max_apply_bytes)
        plan = await run_in_threadpool(make_plan, db, src, data.get("manifest"))
        token = bearer(request) or ""
        payload = {
            "source": sid, "epoch": src["sync_epoch"], "todo": plan["todo"], "removals": plan["removals"],
        }
        cid = confirmations.issue(
            token, f"sync:{sid}", f"epoch:{src['sync_epoch']}", payload=payload,
            ttl=max(config.confirmation_seconds, SYNC_TTL),
        )
        first = [plan["todo"][k]["path"] for k in plan["passes"][0]] if plan["passes"] else []
        out = {
            "confirmation_id": cid, "expires_in": max(config.confirmation_seconds, SYNC_TTL),
            "epoch": src["sync_epoch"], "summary": plan["summary"], "items": plan["items"],
            "items_truncated": plan["items_truncated"], "rejected": plan["rejected"],
            "rejected_count": plan["rejected_count"], "passes": max(len(plan["passes"]), 1),
            "next_pass": first,
        }
        if plan["single_file"]:
            out["single_file"] = True
        return reply(request, 200, out)

    async def sync_apply_ep(request: Request) -> Response:
        sid = request.path_params["sid"]
        src = await run_in_threadpool(get_source, sid)
        data = await read_json(request, config.max_apply_bytes)
        token = bearer(request) or ""
        files_in = data.get("files")
        digest = request_digest(sid, files_in) if isinstance(files_in, list) and all(
            isinstance(f, dict) for f in files_in) else ""
        again = remembered(data.get("confirmation_id"), token, digest) if digest else None
        if again is not None:
            return reply(request, 200, {**again, "replayed": True})
        ok, payload = confirmations.consume_payload(
            data.get("confirmation_id"), token, f"sync:{sid}", f"epoch:{src['sync_epoch']}"
        )
        if not ok or payload is None or payload["source"] != sid or payload["epoch"] != src["sync_epoch"]:
            return fail(request, 409, "confirmation_invalid",
                        "The confirmation is invalid, used up, expired, or the source changed in the meantime. Plan again.")
        files = data.get("files")
        if not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
            raise ValidationError("'files' must be a list.")
        todo: Dict[str, dict] = payload["todo"]
        batch, seen, total = [], set(), 0
        try:
            for f in files:
                path, sha, content = f.get("path"), f.get("sha256"), f.get("content")
                if not isinstance(path, str) or not isinstance(content, str) or not isinstance(sha, str):
                    raise SourceRejected("input", "Every file needs path, sha256 and content (text).")
                _, key = src_rules.clean_relative_path(path)
                planned = todo.get(key)
                if planned is None or key in seen:
                    raise SourceRejected("integrity", f"'{path[:200]}' is not part of the plan or is sent twice.")
                if sha.lower() != planned["sha256"]:
                    raise SourceRejected("integrity", f"'{path[:200]}' differs from the plan.")
                raw = content.encode("utf-8")
                seen.add(key)
                total += len(raw)
                batch.append((planned["path"], raw, sha))
            if len(batch) > PASS_FILES or total > PASS_BYTES:
                raise SourceRejected(
                    "input", f"A pass takes at most {PASS_FILES} files and {PASS_BYTES} bytes. Send the rest in the next pass."
                )
            remaining = {k: v for k, v in todo.items() if k not in seen}
            final = not remaining
            result = await run_in_threadpool(
                db.apply_sync, sid, batch, payload["removals"] if final else [], clock(), SOURCE, payload["epoch"]
            )
        except OutboxError:
            # the confirmation was used up by the attempt; nothing was stored; the client plans again
            raise
        if final:
            out = {"status": "done", **result, "remaining": 0}
            remember(data["confirmation_id"], token, digest, out)
            return reply(request, 200, out)
        next_payload = {**payload, "todo": remaining, "epoch": result["epoch"]}
        cid = confirmations.issue(
            token, f"sync:{sid}", f"epoch:{result['epoch']}", payload=next_payload,
            ttl=max(config.confirmation_seconds, SYNC_TTL),
        )
        nxt = [remaining[k]["path"] for k in sorted(remaining)]
        out = {
            "status": "partial", **result, "remaining": len(remaining), "confirmation_id": cid,
            "next_pass": nxt[:PASS_FILES],
        }
        remember(data["confirmation_id"], token, digest, out)
        return reply(request, 200, out)

    # -- settings, security information, contact import --------------------------------

    def settings_view() -> Dict[str, Any]:
        effective, source, reported = db.admin_candidate_expiry()
        return {"candidate_expiry_days": {
            "value": effective, "source": source, "starting_value": reported,
            "min": db.EXPIRY_MIN, "max": db.EXPIRY_MAX, "default": 30,
            "open_candidates": len(store.pending()),
        }}

    async def get_settings(request: Request) -> Response:
        return reply(request, 200, await run_in_threadpool(settings_view))

    async def put_settings(request: Request) -> Response:
        data = await read_json(request)
        unknown = [k for k in data if k not in ("candidate_expiry_days", "confirmation_id")]
        if unknown or "candidate_expiry_days" not in data:
            raise ValidationError("Only 'candidate_expiry_days' can be changed here (send limits are under /admin/limits).")
        days = data["candidate_expiry_days"]
        if isinstance(days, bool) or not isinstance(days, int) or not db.EXPIRY_MIN <= days <= db.EXPIRY_MAX:
            raise ValidationError(f"'candidate_expiry_days' must be a whole number from {db.EXPIRY_MIN} to {db.EXPIRY_MAX}.")
        current, source, _ = await run_in_threadpool(db.admin_candidate_expiry)
        if source == "stored" and current == days:
            return reply(request, 200, {"status": "unchanged", **(await run_in_threadpool(settings_view))})
        loosening = current is None or days > current  # a longer window keeps candidates alive longer
        if loosening:
            token = bearer(request) or ""
            action = f"setting:candidate_expiry_days:{days}"
            state = hashlib.sha256(f"{current}:{source}".encode()).hexdigest()
            given = data.get("confirmation_id")
            if given is None:
                cid = confirmations.issue(token, action, state)
                return reply(request, 200, {
                    "status": "confirmation_required", "confirmation_id": cid, "expires_in": config.confirmation_seconds,
                    "preview": {"candidate_expiry_days": f"{current if current is not None else 'unknown'} -> {days}"},
                })
            if not confirmations.consume(given, token, action, state):
                return fail(request, 409, "confirmation_invalid",
                            "The confirmation is invalid, used up, expired, or the setting changed in the meantime. Ask again.")
        affected = await run_in_threadpool(store.count_stale, clock(), days) if not loosening else 0
        await run_in_threadpool(db.set_candidate_expiry, days, clock(), SOURCE)
        view = await run_in_threadpool(settings_view)
        return reply(request, 200, {"status": "done", "candidates_expiring_soon": affected, **view})

    async def security_info(request: Request) -> Response:
        """Read-only explanation of how the admin side and the mail connector are protected. Contains no
        secrets, tokens, hashes or keys."""
        def work() -> Dict[str, Any]:
            usage = db.source_usage()
            return {
                "admin_access": {
                    "reachable": "this computer only (loopback); from other devices only through an SSH tunnel",
                    "sign_in": "passphrase, session token in a header, never a cookie",
                    "idle_timeout_seconds": config.idle_seconds, "max_session_seconds": config.max_session_seconds,
                    "failed_sign_ins_before_lockout": config.max_failed_logins, "lockout_seconds": config.lockout_seconds,
                    "confirmation_valid_seconds": config.confirmation_seconds,
                    "allowed_hosts": list(config.allowed_hosts), "allowed_origins": list(config.allowed_origins),
                    "request_size_limit_bytes": config.max_body_bytes, "upload_size_limit_bytes": config.max_apply_bytes,
                },
                "claude_can": [
                    "list the allowed recipients", "prepare a draft (nothing is sent)",
                    "send a prepared draft after your explicit yes", "cancel a draft",
                    "suggest a recipient (you decide outside the chat)", "list the files you allowed",
                ],
                "claude_cannot": [
                    "approve, reject or block a recipient", "change folder or file rules", "change send limits or settings",
                    "read mail", "attach a file that is blocked or not allowed",
                ],
                "mail_limits": {"hard_ceiling_per_hour": HARD_MAX_SENDS_PER_HOUR, "hard_ceiling_per_day": HARD_MAX_SENDS_PER_DAY},
                "files": {
                    "types": {"server_folder": [".md"], "upload": [".md", ".txt"]},
                    "protections": ["names that look sensitive are always refused", "files with secrets are refused",
                                    "a block always wins", "blocked files are deleted at once"],
                    "quotas": {
                        "max_sources": src_rules.MAX_ACTIVE_SOURCES, "max_files_per_source": src_rules.MAX_FILES_PER_SOURCE,
                        "max_file_bytes": src_rules.MAX_FILE_BYTES, "max_total_bytes": src_rules.MAX_TOTAL_BYTES,
                    },
                    "active_sources": usage["sources"],
                },
                "database": {"schema_version": db.schema_version()},
            }
        return reply(request, 200, await run_in_threadpool(work))

    IMPORT_TTL = 900
    IMPORT_SHOWN = 200

    async def import_plan(request: Request) -> Response:
        data = await read_json(request, config.max_apply_bytes)
        report, accepted = await run_in_threadpool(store.classify_import, data.get("rows"))
        counts = {c: 0 for c in ("new", "known", "candidate", "excluded", "invalid", "duplicate")}
        for entry in report:
            counts[entry["category"]] += 1
        import_id = secrets.token_hex(6)
        token = bearer(request) or ""
        passes = max(-(-len(accepted) // store.IMPORT_PASS), 1) if accepted else 0
        payload = {"import_id": import_id, "rows": accepted, "passes": passes, "pass_no": 1, "added": 0}
        cid = confirmations.issue(
            token, "import", import_id, payload=payload, ttl=max(config.confirmation_seconds, IMPORT_TTL)
        )
        shown = [r for r in report if r["category"] != "new"][:IMPORT_SHOWN]
        shown += [r for r in report if r["category"] == "new"][: max(IMPORT_SHOWN - len(shown), 0)]
        return reply(request, 200, {
            "import_id": import_id, "confirmation_id": cid, "expires_in": max(config.confirmation_seconds, IMPORT_TTL),
            "summary": counts, "rows": shown, "rows_truncated": len(report) > len(shown),
            "passes": passes, "per_pass": store.IMPORT_PASS,
            "note": "New addresses become candidates waiting for your decision. Nothing is approved automatically.",
        })

    async def import_apply(request: Request) -> Response:
        data = await read_json(request)
        token = bearer(request) or ""
        given = data.get("confirmation_id")
        again = remembered(given, token, "import")
        if again is not None:
            return reply(request, 200, {**again, "replayed": True})
        item = confirmations.peek_payload(given)
        import_id = item["import_id"] if item else ""
        ok, payload = confirmations.consume_payload(given, token, "import", import_id)
        if not ok or payload is None:
            return fail(request, 409, "confirmation_invalid",
                        "The confirmation is invalid, used up or expired. Plan the import again.")
        rows = payload["rows"]
        batch, rest = rows[: store.IMPORT_PASS], rows[store.IMPORT_PASS:]
        final = not rest
        added, skipped = await run_in_threadpool(
            store.add_import_candidates, batch, clock(), SOURCE, payload["import_id"], payload["pass_no"],
            payload["passes"], payload["added"], final,
        )
        total = payload["added"] + added
        out: Dict[str, Any] = {
            "status": "done" if final else "partial", "import_id": payload["import_id"],
            "pass": payload["pass_no"], "passes": payload["passes"], "added": added, "skipped": skipped,
            "total_added": total, "remaining": len(rest),
        }
        if not final:
            nxt = {**payload, "rows": rest, "pass_no": payload["pass_no"] + 1, "added": total}
            out["confirmation_id"] = confirmations.issue(
                token, "import", payload["import_id"], payload=nxt, ttl=max(config.confirmation_seconds, IMPORT_TTL)
            )
        remember(given, token, "import", out)
        return reply(request, 200, out)

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
        Route("/admin/sources", endpoint(list_sources_ep), methods=["GET"]),
        Route("/admin/sources", endpoint(create_source_ep), methods=["POST"]),
        Route("/admin/sources/{sid}/rename", endpoint(rename_source_ep), methods=["POST"]),
        Route("/admin/sources/{sid}/remove", endpoint(remove_source_ep), methods=["POST"]),
        Route("/admin/sources/{sid}/evaluate", endpoint(evaluate_ep), methods=["POST"]),
        Route("/admin/sources/{sid}/rule", endpoint(source_rule_ep), methods=["PUT"]),
        Route("/admin/sources/{sid}/folders", endpoint(source_folders_ep), methods=["GET"]),
        Route("/admin/sources/{sid}/files", endpoint(source_files_ep), methods=["GET"]),
        Route("/admin/sources/{sid}/sync/plan", endpoint(sync_plan_ep), methods=["POST"]),
        Route("/admin/sources/{sid}/sync/apply", endpoint(sync_apply_ep), methods=["POST"]),
        Route("/admin/audit", endpoint(audit), methods=["GET"]),
        Route("/admin/settings", endpoint(get_settings), methods=["GET"]),
        Route("/admin/settings", endpoint(put_settings), methods=["PUT"]),
        Route("/admin/security", endpoint(security_info), methods=["GET"]),
        Route("/admin/recipients/import/plan", endpoint(import_plan), methods=["POST"]),
        Route("/admin/recipients/import/apply", endpoint(import_apply), methods=["POST"]),
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
