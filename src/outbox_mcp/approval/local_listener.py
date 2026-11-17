"""The local approval page for the stdio server.

The stdio server talks to the assistant over standard input and output, so it has no web server
of its own. For the local approval origin (`http://localhost:<port>`) this module starts a small
one next to it, in a background thread.

Rules that keep it as strict as the public setup:
- It binds to 127.0.0.1 only, never to another interface.
- It serves exactly the approval and enrollment routes, with the same Host, Origin, size and
  rate checks as the public process. No sign-in is added: the passkey is the proof.
- If no port can be taken (the configured one and a few after it), there is NO listener and the
  caller keeps approval switched off. Mail to external recipients then stays unsendable. Nothing
  falls back to a weaker check; a busy port only moves the page to the next free port.
- It never writes to standard output (that is the protocol channel of the stdio server).
"""

from __future__ import annotations

import socket
import sys
import threading
from dataclasses import dataclass, replace
from typing import Optional, Tuple

from starlette.applications import Starlette

from .service import ApprovalConfig, ApprovalService
from .web import AttemptLimiter, build_approval_routes

LOOPBACK = "127.0.0.1"
PORT_TRIES = 10  # the configured port and the nine after it; an internal limit, not a setting


@dataclass
class LocalListener:
    port: int
    error: Optional[str] = None
    _server: object = None
    _thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self.error is None and self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def _bind_first_free(first: int) -> Tuple[Optional[socket.socket], int, Optional[OSError]]:
    """Bind the first free port from `first` on. No SO_REUSEADDR/REUSEPORT: if anything else holds
    a port, binding that port must fail, and the next one is tried. Returns (socket, port, None),
    or (None, last port tried, the last error) when every port in the range is taken."""
    last_error: Optional[OSError] = None
    last = min(first + PORT_TRIES, 65536) - 1
    for port in range(first, last + 1):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((LOOPBACK, port))
            sock.listen(16)
            return sock, port, None
        except OSError as exc:
            sock.close()
            last_error = exc
    return None, last, last_error


def find_running_page(config: ApprovalConfig) -> Optional[int]:
    """The port on which a Mail Assistant approval page of this kind answers right now, or None.

    Used by the admin command that prints the one-time passkey link: the stdio server may have
    moved to a later port, and the person should not have to know that. A port counts only if the
    answer under the right Host is this page's own style sheet; some other program on the port
    does not count."""
    if not config.is_local or config.port is None:
        return None
    import urllib.error
    import urllib.request

    from .web import APP_CSS

    last = min(int(config.port) + PORT_TRIES, 65536) - 1
    for port in range(int(config.port), last + 1):
        request = urllib.request.Request(
            f"http://{LOOPBACK}:{port}/approval/style.css", headers={"Host": f"localhost:{port}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=0.5) as response:
                if response.read(len(APP_CSS) + 64).decode("utf-8", "replace") == APP_CSS:
                    return port
        except (OSError, urllib.error.URLError, ValueError):
            continue
    return None


def start_local_listener(service: ApprovalService, send=None) -> LocalListener:
    """Start the page. Never raises: a failure comes back in `.error` and means "no listener".

    The configured port is only the first one tried. Several stdio servers can run at the same
    time (Claude Desktop may start more than one), and each needs its own port. The page that
    answers on a port knows only that port: `service.config` is replaced by the same configuration
    with the port that was actually taken, before any route is built, so every origin, Host and
    link check uses the real port, exactly as strict as before. A passkey for `localhost` does not
    depend on the port, and the drafts, challenges and passkeys live in the shared database, so an
    approval link from any instance works on that instance's page."""
    config = service.config
    if not config.is_local:
        return LocalListener(port=0, error="Not a local approval setup.")
    first = int(config.port)
    sock, port, exc = _bind_first_free(first)
    if sock is None:
        handle = LocalListener(port=first)
        handle.error = (f"No free port from {first} to {port} on {LOOPBACK} "
                        f"({exc.__class__.__name__ if exc else 'OSError'}).")
        return handle
    handle = LocalListener(port=port)
    if port != first:
        service.config = replace(config, port=port)
        print(f"Mail Assistant: port {first} is in use, the approval page runs on port {port}.",
              file=sys.stderr)

    import uvicorn

    app = Starlette(routes=build_approval_routes(service, AttemptLimiter(), send=send))
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", access_log=False, log_config=None, lifespan="off")
    )
    handle._server = server

    def run() -> None:
        try:
            server.run(sockets=[sock])
        except BaseException:  # the thread must never take the stdio server down
            handle.error = "The local approval page stopped unexpectedly."
        finally:
            sock.close()

    thread = threading.Thread(target=run, name="approval-listener", daemon=True)
    handle._thread = thread
    thread.start()
    return handle
