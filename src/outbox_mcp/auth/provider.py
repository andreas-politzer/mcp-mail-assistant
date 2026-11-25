"""The authorization server's brain, written against the MCP SDK's provider protocol.

Rules enforced here (each one has tests):
* redirect addresses are compared exactly against an allowlist, at registration, at
  authorize time and again when the code is issued;
* every token is bound to this server's own resource address;
* only the configured scopes can ever be granted;
* codes and refresh tokens are single use, reuse burns the whole token family.
"""

from __future__ import annotations

import logging

from pydantic import AnyUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .config import AuthConfig
from .redirects import is_allowed_redirect_uri
from .resource import same_resource
from .store import AuthStore, ClientLimitReached, PendingLimitReached

log = logging.getLogger(__name__)


class OutboxAuthProvider:
    def __init__(self, config: AuthConfig, store: AuthStore):
        self.config = config
        self.store = store

    # ---- clients --------------------------------------------------------------------
    async def get_client(self, client_id: str):
        return self.store.get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.token_endpoint_auth_method != "none":
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="Only public clients (token_endpoint_auth_method=none) are supported.",
            )
        for uri in client_info.redirect_uris or []:
            if not is_allowed_redirect_uri(str(uri), self.config.redirect_uris):
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description="This redirect address is not allowed.",
                )
        if not client_info.redirect_uris:
            raise RegistrationError(error="invalid_redirect_uri", error_description="A redirect address is required.")
        try:
            self.store.register_client(
                client_info,
                max_clients=self.config.max_clients,
                max_unused=self.config.max_unused_clients,
                prune_before=self.store.now() - self.config.client_prune_seconds,
            )
        except ClientLimitReached as limit:
            log.warning(
                "client registration refused: %s clients %d of %d. Free space with "
                "'python -m outbox_mcp.auth.admin prune --db <auth.db>' or raise "
                "OUTBOX_OAUTH_MAX_CLIENTS / OUTBOX_OAUTH_MAX_UNUSED_CLIENTS.",
                "never-signed-in" if limit.kind == "never_signed_in" else "registered",
                limit.count, limit.limit)
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="Too many registered clients.",
            )

    # ---- authorize: only creates a pending login, never a code ----------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        redirect = str(params.redirect_uri)
        registered = [str(u) for u in client.redirect_uris or []]
        if redirect not in registered or not is_allowed_redirect_uri(redirect, self.config.redirect_uris):
            raise AuthorizeError(error="invalid_request", error_description="Redirect address not allowed.")
        if params.resource is not None and not same_resource(params.resource, self.config.resource_url):
            raise AuthorizeError(error="invalid_request", error_description="Unknown resource.")
        scopes = list(params.scopes) if params.scopes else list(self.config.scopes)
        if not set(scopes) <= set(self.config.scopes):
            raise AuthorizeError(error="invalid_scope", error_description="Scope not allowed.")
        try:
            request_id = self.store.create_pending(
                client.client_id,
                {
                    "redirect_uri": redirect,
                    "redirect_explicit": params.redirect_uri_provided_explicitly,
                    "state": params.state,
                    "scopes": scopes,
                    "code_challenge": params.code_challenge,
                },
                ttl=self.config.pending_ttl,
                max_pending=self.config.max_pending_requests,
            )
        except PendingLimitReached:
            raise AuthorizeError(error="temporarily_unavailable",
                                 error_description="Too many open logins, try again in a few minutes.")
        return f"{self.config.login_url}?request={request_id}"

    # ---- code exchange --------------------------------------------------------------
    async def load_authorization_code(self, client, authorization_code: str):
        row = self.store.load_code(authorization_code, client.client_id)
        if row is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=row.scopes,
            expires_at=row.expires_at,
            client_id=row.client_id,
            code_challenge=row.code_challenge,
            redirect_uri=AnyUrl(row.redirect_uri),
            redirect_uri_provided_explicitly=row.redirect_explicit,
            resource=row.resource,
            subject=row.subject,
        )

    async def exchange_authorization_code(self, client, authorization_code) -> OAuthToken:
        issued = self.store.redeem_code(
            authorization_code.code,
            client.client_id,
            access_ttl=self.config.access_token_ttl,
            refresh_ttl=self.config.refresh_token_ttl,
        )
        if issued is None:
            raise TokenError(error="invalid_grant", error_description="Code is invalid or already used.")
        return self._token(issued)

    # ---- refresh --------------------------------------------------------------------
    async def load_refresh_token(self, client, refresh_token: str):
        row = self.store.load_refresh(refresh_token, client.client_id)
        if row is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row.client_id,
            scopes=row.scopes,
            expires_at=int(row.expires_at),
            resource=row.resource,
            subject=row.subject,
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes: list[str]) -> OAuthToken:
        issued = self.store.rotate_refresh(
            refresh_token.token,
            client.client_id,
            scopes,
            access_ttl=self.config.access_token_ttl,
            refresh_ttl=self.config.refresh_token_ttl,
        )
        if issued is None:
            raise TokenError(error="invalid_grant", error_description="Refresh token is invalid.")
        return self._token(issued)

    # ---- tokens ---------------------------------------------------------------------
    async def load_access_token(self, token: str):
        row = self.store.lookup_access(token)
        if row is None:
            return None
        return AccessToken(
            token=token, client_id=row.client_id, scopes=row.scopes,
            expires_at=int(row.expires_at), resource=row.resource, subject=row.subject,
        )

    async def revoke_token(self, token) -> None:
        self.store.revoke_token(token.token)

    @staticmethod
    def _token(issued) -> OAuthToken:
        return OAuthToken(
            access_token=issued.access_token,
            token_type="Bearer",
            expires_in=issued.expires_in,
            scope=" ".join(issued.scopes),
            refresh_token=issued.refresh_token,
        )
