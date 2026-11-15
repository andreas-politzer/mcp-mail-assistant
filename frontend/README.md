# Desktop app

The admin app of the Mail Assistant: Tauri 2 for the window, React 19 with Tailwind 4 for the interface, Rust for everything that touches files on your computer. It talks to the admin API of the server. See the [main README](../README.md#desktop-app) for how to connect, the system table and the security model.

## Run

```bash
npm install
npm run tauri dev      # window with live reload (needs Rust and the Tauri system packages)
npm run dev            # interface only in a browser; file features need the app
npm run tauri build    # installable package for the current system
```

## Tests

```bash
npm test                                # interface and API flows against a real admin API
npx tsc -b                              # types
cargo test --manifest-path src-tauri/Cargo.toml   # local file access (Rust)
```

The interface tests start the real admin API (Python) with a temporary database. They need the project's Python environment: set `ADMIN_PYTHON` to its interpreter if it is not `.venv/bin/python` in the repository root. More in [tests/README.md](tests/README.md).

## Layout

```
src/            interface (boot sequence, desktop, tiles: recipients, files, settings, activity, help)
src/api/        admin API client and session handling
src/tiles/      the five application tiles
src/components/ retro desktop pieces (icons, windows, Funki, clock, sticky note)
src-tauri/      Rust: link store, folder scan, file reading, shutdown
tests/          vitest tests with a stand-in for the Rust side
```

## Rules for working on it

- Application tiles never get a `z-index`: it breaks the glass blur. Playful pieces that must lie under the tiles are drawn before them in `src/App.tsx`.
- Nothing private in windows, help texts, tests or examples: no real server names, addresses or paths. `tests/test_repo_hygiene.py` in the main test suite checks this.
- A local folder's path stays on the computer. The interface gets display texts and ids, never a path to read.
