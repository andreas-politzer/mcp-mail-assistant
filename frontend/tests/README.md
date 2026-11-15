# Front-end tests

`npm test` runs the window code (the Files area and shutting down) against a **real admin API** that
the test run starts itself (temporary database, a notes folder, a known passphrase) and against a
stand-in for the Rust side (`tests/support/fakeTauri.ts`) that follows the same rules as
`src-tauri/src/local.rs`. The Rust side has its own tests: `cd src-tauri && cargo test`.

Needs `uv` (it provides the project's Python environment plus `uvicorn` for the run), or set
`ADMIN_PYTHON` to a Python that has the project and `uvicorn` installed.
Nothing here touches your real server or your real database.
