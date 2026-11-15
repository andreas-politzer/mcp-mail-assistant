# 0001 One repository, backend and frontend kept apart

Status: planned (the frontend has not been started).

## Context

The server (Python) is the only place where rules are enforced. A small admin app for recipients and attachment folders is planned. It will be a React/TypeScript interface, first used in a browser and later wrapped in a Tauri 2 desktop window.

## Decision

Keep one repository. The Python server stays in `src/outbox_mcp/`. The interface gets its own top-level folder (`frontend/`), and the desktop wrapper sits inside or beside it. The interface contains **no business logic**: it shows state and sends requests to an admin API. The server decides.

## Consequences

- A manipulated or buggy interface cannot widen any permission; the server checks every request.
- The interface can be developed and tested in a plain browser before the Tauri wrapper exists.
- Backend tests and release stay independent of the frontend toolchain.
