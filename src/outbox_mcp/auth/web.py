"""HTTP side of the authorization server: login page, OAuth routes, metadata."""

from __future__ import annotations

import hmac
import html
import secrets
import threading
from urllib.parse import parse_qsl, urlsplit

from pydantic import AnyHttpUrl
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import request_response

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import construct_redirect_uri
from mcp.server.auth.routes import build_metadata, cors_middleware, create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions

from .config import AuthConfig
from .passwords import verify_password
from .provider import OutboxAuthProvider
from .redirects import is_allowed_redirect_uri
from .store import AuthStore

CSRF_COOKIE = "outbox_csrf"
METADATA_PATH = "/.well-known/oauth-authorization-server"
SUBJECT = "owner"

_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action {form_action}; frame-ancestors 'none'"

_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": _CSP.format(form_action="'self'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

_PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>outbox-mcp Anmeldung</title>
<style>body{{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem}}
input[type=password]{{width:100%;padding:.6rem;margin:.5rem 0 1rem;box-sizing:border-box}}
button{{padding:.6rem 1.2rem;margin-right:.5rem}}.err{{color:#b00020}}</style></head>
<body><h1>outbox-mcp</h1>
<p><strong>{client}</strong> m&ouml;chte E-Mails &uuml;ber deinen Postausgang senden (Zugriff: <code>{scopes}</code>).</p>
<p>R&uuml;ckkehr zu: <code>{host}</code></p>
<p><strong>Nur fortfahren, wenn du selbst gerade Claude mit deinem Postausgang verbunden hast.</strong>
Hast du das nicht getan, klicke auf &bdquo;Ablehnen&ldquo; oder schlie&szlig;e diese Seite.</p>
{error}
<form method="post" action="/login">
<input type="hidden" name="request" value="{request}">
<input type="hidden" name="csrf" value="{csrf}">
<label>Passwort<input type="password" name="password" autocomplete="current-password" autofocus></label>
<button type="submit" name="action" value="approve">Erlauben</button>
<button type="submit" name="action" value="deny">Ablehnen</button>
</form></body></html>"""


def _respond(content: str, status: int = 200, extra: dict | None = None) -> HTMLResponse:
    headers = dict(_HEADERS)
    headers.update(extra or {})
    return HTMLResponse(content, status_code=status, headers=headers)


def _plain(status: int, text: str, extra: dict | None = None) -> Response:
    headers = dict(_HEADERS)
    headers.update(extra or {})
    return Response(text, status_code=status, headers=headers, media_type="text/plain; charset=utf-8")


async def _read_form(request: Request, limit: int):
    """Read a small urlencoded form. Returns (fields, None) or (None, error response)."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return None, _plain(415, "Nur Formulardaten erlaubt.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None, _plain(413, "Anfrage zu gross.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            return None, _plain(413, "Anfrage zu gross.")
    fields = dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))
    return fields, None


def build_routes(config: AuthConfig, store: AuthStore, provider: OutboxAuthProvider) -> list[Route]:
    """Login page plus the SDK's OAuth endpoints (authorize, token, register, revoke)."""

    # Chromium also applies form-action to the redirect that follows the POST, so the origins
    # we are allowed to send the user back to must be listed (and only those).
    return_origins = []
    for uri in config.redirect_uris:
        parts = urlsplit(uri)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in return_origins:
            return_origins.append(origin)
    page_csp = _CSP.format(form_action=" ".join(["'self'", *return_origins]))

    def render(request_id: str, pending, csrf: str, status: int = 200, error: str = "") -> HTMLResponse:
        client = store.get_client(pending.client_id)
        name = (client.client_name if client and client.client_name else "Ein Programm")
        host = urlsplit(pending.params["redirect_uri"]).netloc
        body = _PAGE.format(
            client=html.escape(name), scopes=html.escape(" ".join(pending.params["scopes"])),
            host=html.escape(host), request=html.escape(request_id), csrf=html.escape(csrf),
            error=f'<p class="err">{html.escape(error)}</p>' if error else "",
        )
        resp = _respond(body, status, {"Content-Security-Policy": page_csp})
        resp.set_cookie(CSRF_COOKIE, csrf, path="/login", httponly=True, samesite="strict",
                        secure=config.secure_cookies, max_age=config.pending_ttl)
        return resp

    async def login_get(request: Request) -> Response:
        request_id = request.query_params.get("request", "")
        pending = store.get_pending(request_id)
        if pending is None:
            return _plain(400, "Anmeldeanfrage unbekannt oder abgelaufen. Bitte neu starten.")
        csrf = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        return render(request_id, pending, csrf)

    password_gate = threading.BoundedSemaphore(config.max_parallel_password_checks)

    def check_password(password: str) -> bool:
        # scrypt is deliberately expensive: never run more than a few checks at once.
        with password_gate:
            return verify_password(password, config.password_hash)

    async def login_post(request: Request) -> Response:
        form, problem = await _read_form(request, config.max_login_body)
        if problem is not None:
            return problem
        request_id = str(form.get("request", ""))
        field = str(form.get("csrf", ""))
        cookie = request.cookies.get(CSRF_COOKIE, "")
        if not field or not cookie or not hmac.compare_digest(field, cookie):
            return _plain(403, "Ungueltige Anfrage.")
        pending = store.get_pending(request_id)
        if pending is None:
            return _plain(400, "Anmeldeanfrage unbekannt oder abgelaufen. Bitte neu starten.")

        redirect_uri = pending.params["redirect_uri"]
        client = store.get_client(pending.client_id)
        if (client is None or redirect_uri not in [str(u) for u in client.redirect_uris or []]
                or not is_allowed_redirect_uri(redirect_uri, config.redirect_uris)):
            store.consume_pending(request_id)
            return _plain(400, "Rueckkehradresse nicht erlaubt.")

        if str(form.get("action", "")) == "deny":
            store.consume_pending(request_id)
            target = construct_redirect_uri(redirect_uri, error="access_denied",
                                            state=pending.params.get("state"))
            return RedirectResponse(target, status_code=303, headers=dict(_HEADERS))

        # Claim the attempt first (atomic), then do the expensive check.
        wait, attempt_id = store.reserve_login_attempt(config.max_login_failures, config.login_lockout_seconds)
        if wait > 0:
            return _plain(429, "Zu viele Fehlversuche. Bitte spaeter erneut versuchen.",
                          {"Retry-After": str(int(wait) + 1)})
        password = str(form.get("password", ""))
        ok = await run_in_threadpool(check_password, password)
        store.finish_login_attempt(attempt_id, ok)
        if not ok:
            left = store.fail_pending(request_id, config.max_attempts_per_request)
            if left <= 0:
                return _plain(401, "Falsches Passwort. Anfrage beendet, bitte neu starten.")
            return render(request_id, pending, cookie, 401, "Falsches Passwort.")

        taken = store.consume_pending(request_id)
        if taken is None:
            return _plain(400, "Anmeldeanfrage unbekannt oder abgelaufen. Bitte neu starten.")
        code = store.create_code(
            client_id=taken.client_id,
            scopes=taken.params["scopes"],
            code_challenge=taken.params["code_challenge"],
            redirect_uri=redirect_uri,
            redirect_explicit=bool(taken.params.get("redirect_explicit", True)),
            resource=config.resource_url,
            subject=SUBJECT,
            ttl=config.auth_code_ttl,
        )
        target = construct_redirect_uri(redirect_uri, code=code, state=taken.params.get("state"))
        return RedirectResponse(target, status_code=303, headers=dict(_HEADERS))

    registration = ClientRegistrationOptions(
        enabled=True, valid_scopes=list(config.scopes), default_scopes=list(config.scopes))
    revocation = RevocationOptions(enabled=True)
    issuer = AnyHttpUrl(config.issuer_url)
    sdk_routes = create_auth_routes(provider, issuer, client_registration_options=registration,
                                    revocation_options=revocation)
    sdk_routes = [r for r in sdk_routes if getattr(r, "path", None) not in (METADATA_PATH, "/revoke")]

    authenticator = ClientAuthenticator(provider)

    async def revoke(request: Request) -> Response:
        # The SDK's own revoke handler demands a client_secret field even from public
        # clients (which have none), so Claude's revoke calls would fail. Same rules, lenient form.
        try:
            client = await authenticator.authenticate_request(request)
        except AuthenticationError:
            return Response('{"error":"unauthorized_client"}', status_code=401,
                            media_type="application/json", headers=dict(_HEADERS))
        form = await request.form()
        token = str(form.get("token", ""))
        if not token:
            return Response('{"error":"invalid_request"}', status_code=400,
                            media_type="application/json", headers=dict(_HEADERS))
        row = store.lookup_access(token) or store.load_refresh(token, client.client_id)
        if row is not None and row.client_id == client.client_id:
            store.revoke_token(token)
        return Response(status_code=200, headers=dict(_HEADERS))

    revoke_route = Route(
        "/revoke",
        endpoint=CORSMiddleware(request_response(revoke), allow_origins="*", allow_methods=["POST", "OPTIONS"]),
        methods=["POST", "OPTIONS"],
    )

    # Only public clients are supported (Claude registers as one), so advertise just "none".
    metadata = build_metadata(issuer, None, registration, revocation)
    metadata.token_endpoint_auth_methods_supported = ["none"]
    metadata.revocation_endpoint_auth_methods_supported = ["none"]
    metadata_route = Route(
        METADATA_PATH,
        endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
        methods=["GET", "OPTIONS"],
    )
    return [
        Route("/login", login_get, methods=["GET"]),
        Route("/login", login_post, methods=["POST"]),
        metadata_route,
        revoke_route,
        *sdk_routes,
    ]
