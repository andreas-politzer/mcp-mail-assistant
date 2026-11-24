"""OAuth 2.1 for the HTTP transport.

This package is deliberately independent of the mail code: it knows nothing about
e-mail, SMTP or drafts. It issues and checks tokens, and it can protect any MCP
server. The authorization server (store, provider, login page) and the resource
server (``StoreTokenVerifier``) only meet at the ``AuthStore`` interface, so the two
halves can be split into separate processes later without touching the mail side.
"""
