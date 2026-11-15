# 0003 Admin access never goes through the public tunnel

Status: planned (today the admin path is the command line on the server).

## Context

The assistant reaches the server through a public tunnel and signs in with OAuth. An admin interface can change what the assistant is allowed to do, so it must not be reachable by the same route or with the same credentials.

## Decision

- The admin API has its **own sign-in**, separate from the OAuth system used by the assistant.
- It is reachable only locally or over a private network, never through the public tunnel.
- It is used rarely, so sessions can be short.
- The admin API is a client-independent interface; the web page and the desktop app are two clients of it.

## Consequences

- Compromising the assistant connection does not open the admin side.
- The admin API must be designed and reviewed before any interface is built on it.
