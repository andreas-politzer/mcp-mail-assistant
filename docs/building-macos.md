# Building the macOS app

The app is built for the chip of the Mac it is built on (Apple Silicon or Intel). There is no universal package:
PyInstaller would need universal versions of every dependency, and the download would double in size. Build once
per chip.

```bash
bash packaging/build-macos.sh
```

What it does:

1. Builds the server program (`mail-assistant-server`) with PyInstaller into `packaging/dist/`.
2. Checks it with `packaging/check-macos.sh`.
3. Copies it into `frontend/src-tauri/resources/server/` and runs `tauri build` with
   `frontend/src-tauri/tauri.bundle.conf.json`, which adds that folder to the app's resources. Normal
   development and `cargo test` do not use this file, so they need no bundled server.
4. Checks the program inside the finished `Mail Assistant.app` once more.

`check-macos.sh` takes the path of a server program and checks `version`, `setup status`, `local api` (start and one
request), `stdio` (the handshake Claude Desktop does) and that `admin` and `serve` load all their modules. It uses
a throw-away data folder and never touches the real keychain or the real Claude Desktop settings.

The finished app is not signed. On another Mac, macOS warns about an unverified developer on the first start.
