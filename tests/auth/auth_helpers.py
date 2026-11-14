"""Constants and helpers for the OAuth tests.

Everything here runs against the real ASGI app through Starlette's TestClient.
No mail code is imported: the protected "resource" is a trivial ``ping`` tool.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from urllib.parse import parse_qs, urlparse



ISSUER = "https://outbox.example.test"
RESOURCE = ISSUER + "/mcp"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
PASSWORD = "correct horse battery staple"


class FakeClock:
    """Starts at the real time and only moves when told to."""

    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def add_ping(mcp):
    @mcp.tool()
    def ping() -> str:
        """Harmless test tool."""
        return "pong"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class Flow:
    """Small helper that walks the OAuth steps over HTTP."""

    def __init__(self, client, store, clock):
        self.client = client
        self.store = store
        self.clock = clock

    # --- building blocks -------------------------------------------------
    def register(self, redirect_uris=(CALLBACK,), **extra):
        body = {
            "redirect_uris": list(redirect_uris),
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "Claude",
            "scope": "mail:send",
        }
        body.update(extra)
        return self.client.post("/register", json=body)

    def new_client_id(self, **kw):
        resp = self.register(**kw)
        assert resp.status_code == 201, resp.text
        return resp.json()["client_id"]

    @staticmethod
    def pkce():
        verifier = _b64(secrets.token_bytes(32))
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        return verifier, challenge

    def authorize(self, client_id, challenge, *, redirect_uri=CALLBACK, state="st-123",
                  resource=None, scope="mail:send", method="S256"):
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": scope,
        }
        if challenge is not None:
            params["code_challenge"] = challenge
            params["code_challenge_method"] = method
        if resource is not None:
            params["resource"] = resource
        return self.client.get("/authorize", params=params)

    @staticmethod
    def query_of(response):
        return parse_qs(urlparse(response.headers["location"]).query)

    def request_id(self, authorize_response):
        assert authorize_response.status_code == 302, authorize_response.text
        return self.query_of(authorize_response)["request"][0]

    def login_page(self, request_id):
        resp = self.client.get("/login", params={"request": request_id})
        return resp

    @staticmethod
    def csrf_of(page):
        match = re.search(r'name="csrf" value="([^"]+)"', page.text)
        assert match, page.text
        return match.group(1)

    def login(self, request_id, password=PASSWORD, *, csrf="auto", action="approve"):
        if csrf == "auto":
            csrf = self.csrf_of(self.login_page(request_id))
        data = {"request": request_id, "password": password, "action": action}
        if csrf is not None:
            data["csrf"] = csrf
        return self.client.post("/login", data=data)

    def exchange(self, client_id, code, verifier, *, redirect_uri=CALLBACK):
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
        }
        if verifier is not None:
            data["code_verifier"] = verifier
        return self.client.post("/token", data=data)

    def refresh(self, client_id, refresh_token):
        return self.client.post("/token", data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        })

    def revoke(self, client_id, token, hint=None):
        data = {"token": token, "client_id": client_id}
        if hint:
            data["token_type_hint"] = hint
        return self.client.post("/revoke", data=data)

    def mcp_initialize(self, token=None):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        return self.client.post("/mcp", json=body, headers=headers)

    # --- whole flow ------------------------------------------------------
    def full_login(self, **authorize_kw):
        """Register, authorize, log in, exchange. Returns a dict."""
        client_id = self.new_client_id()
        verifier, challenge = self.pkce()
        request_id = self.request_id(self.authorize(client_id, challenge, **authorize_kw))
        done = self.login(request_id)
        assert done.status_code == 303, done.text
        code = self.query_of(done)["code"][0]
        tok = self.exchange(client_id, code, verifier)
        assert tok.status_code == 200, tok.text
        body = tok.json()
        return {
            "client_id": client_id, "verifier": verifier, "code": code,
            "access": body["access_token"], "refresh": body["refresh_token"],
            "body": body,
        }
