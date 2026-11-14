"""Resource-server side: decides whether a bearer token may use the MCP endpoint.

It asks the token store directly (same process), so there is no network round trip
and nothing an outsider could call to probe tokens.
"""

from __future__ import annotations

from mcp.server.auth.provider import AccessToken

from .store import AuthStore


class StoreTokenVerifier:
    def __init__(self, store: AuthStore, resource_url: str):
        self.store = store
        self.resource_url = resource_url

    async def verify_token(self, token: str):
        row = self.store.lookup_access(token)
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row.client_id,
            scopes=row.scopes,
            expires_at=int(row.expires_at),
            resource=row.resource,
            subject=row.subject,
        )
