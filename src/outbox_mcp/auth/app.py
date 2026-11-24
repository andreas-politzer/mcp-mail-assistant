"""Builds the ASGI app: MCP resource server plus the authorization server routes."""

from __future__ import annotations

from typing import Callable, Optional

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .config import AuthConfig
from .provider import OutboxAuthProvider
from .store import AuthStore
from .verifier import StoreTokenVerifier
from .web import build_routes


def create_authenticated_app(
    config: AuthConfig,
    store: AuthStore,
    *,
    configure: Optional[Callable[[MCPServer], None]] = None,
    json_response: bool = False,
    stateless_http: bool = False,
    name: str = "outbox-mcp",
    server_options: Optional[dict] = None,
):
    hosts = [config.host, f"{config.host}:*"]
    if config.host in ("localhost", "127.0.0.1"):
        hosts += ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    mcp = MCPServer(
        name,
        **(server_options or {}),
        token_verifier=StoreTokenVerifier(store, config.resource_url),
        auth=AuthSettings(
            issuer_url=config.issuer_url,
            resource_server_url=config.resource_url,
            required_scopes=list(config.scopes),
            validate_token_resource=True,
        ),
    )
    if configure is not None:
        configure(mcp)
    app = mcp.streamable_http_app(
        streamable_http_path=config.mcp_path,
        json_response=json_response,
        stateless_http=stateless_http,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=[f"https://{config.host}", f"http://{config.host}"],
        ),
    )
    provider = OutboxAuthProvider(config, store)
    app.router.routes.extend(build_routes(config, store, provider))
    return app
