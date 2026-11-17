"""The approval and enrollment pages, served by the public process next to the sign-in.

Rules: opening a page (GET) never changes anything; only POST does. Every answer carries strict
headers (no caching, no framing, no referrer, no inline script or style). A POST must come from
this page: JSON content type and an Origin that is exactly the approval origin. The pages show the
full draft; what a person approves is what they can read there.
"""

from __future__ import annotations

import html
import json
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional
from urllib.parse import urlsplit

import anyio
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .service import ApprovalService

MAX_BODY_BYTES = 64 * 1024
POSTS_PER_MINUTE = 30       # across all approval and enrollment POSTs
FAILURES_PER_TARGET = 5     # failed attempts per draft or enrollment link, until it expires

_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

APP_JS = r'''"use strict";
function fromB64(v){const p="=".repeat((4-v.length%4)%4);const b=atob((v+p).replace(/-/g,"+").replace(/_/g,"/"));return Uint8Array.from(b,c=>c.charCodeAt(0));}
function toB64(v){const b=new Uint8Array(v);let s="";for(const x of b)s+=String.fromCharCode(x);return btoa(s).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/g,"");}
function pack(c){const r=c.response;const o={id:c.id,rawId:toB64(c.rawId),type:c.type,response:{},clientExtensionResults:c.getClientExtensionResults()};
for(const k of ["clientDataJSON","attestationObject","authenticatorData","signature","userHandle"]){if(r[k])o.response[k]=toB64(r[k]);}
if(r.getTransports)o.response.transports=r.getTransports();return o;}
async function post(url,body){const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),cache:"no-store"});
let d={};try{d=await r.json();}catch(_){}
if(!r.ok||d.ok===false)throw new Error(d.message||("HTTP "+r.status));return d;}
function showHelp(){const h=document.getElementById("help");if(h)h.hidden=false;}
async function go(){
const out=document.getElementById("result"),btn=document.getElementById("go"),base=document.body.dataset.base,kind=document.body.dataset.kind;
if(!window.PublicKeyCredential||!navigator.credentials){out.textContent="This window cannot use passkeys.";showHelp();return;}
btn.disabled=true;out.textContent="Waiting for the passkey…";
try{
const o=await post(base+"/options",{});const pk=o.publicKey;pk.challenge=fromB64(pk.challenge);
let cred;
if(kind==="enroll"){pk.user.id=fromB64(pk.user.id);(pk.excludeCredentials||[]).forEach(c=>{c.id=fromB64(c.id);});cred=await navigator.credentials.create({publicKey:pk});}
else{(pk.allowCredentials||[]).forEach(c=>{c.id=fromB64(c.id);});cred=await navigator.credentials.get({publicKey:pk});}
const d=await post(base+"/verify",{challenge_id:o.challenge_id,credential:pack(cred)});
out.textContent=d.message;btn.hidden=true;
if(kind==="approve"){const done=document.getElementById("done");if(done){out.textContent="";
if(d.title){const t=document.getElementById("done-title"),x=document.getElementById("done-text");if(t)t.textContent=d.title;if(x)x.textContent=d.detail||"";if(d.outcome!=="sent")done.classList.add("warn");}
done.hidden=false;done.scrollIntoView();}}
}catch(e){out.textContent="Not done: "+e.message;btn.disabled=false;showHelp();}}
const goBtn=document.getElementById("go");if(goBtn)goBtn.addEventListener("click",go);
document.querySelectorAll("time[data-ts]").forEach(t=>{const d=new Date(Number(t.dataset.ts)*1000);if(!isNaN(d.getTime()))t.textContent=d.toLocaleString();});
const copyBtn=document.getElementById("copy");
if(copyBtn)copyBtn.addEventListener("click",async()=>{const c=document.getElementById("copied");let ok=false;try{await navigator.clipboard.writeText(window.location.href);ok=true;}catch(_){}
if(c){c.textContent=ok?"Copied.":"Could not copy. Select the address bar text instead.";c.hidden=false;}});
const closeBtn=document.getElementById("close");
if(closeBtn)closeBtn.addEventListener("click",()=>{window.close();setTimeout(()=>{const h=document.getElementById("closehint");if(h)h.hidden=false;},400);});
'''

APP_CSS = '''body{font-family:system-ui,sans-serif;max-width:40rem;margin:1rem auto;padding:0 1rem;line-height:1.4}
dt{font-weight:600;margin-top:.8rem}dd{margin:0;word-break:break-word}
pre.text{white-space:pre-wrap;word-break:break-word;max-height:22rem;overflow:auto;border:1px solid #888;padding:.6rem;border-radius:6px;margin:0}
button{font-size:1.1rem;padding:.7rem 1.2rem;margin-top:1rem}
.note{color:#555;font-size:.9rem}
.warn{margin-top:1rem;padding:.8rem 1rem;border:2px solid #b26a00;border-radius:8px;background:#fff4e0}
.done.warn{border-color:#b26a00;background:#fff4e0}
.done{margin-top:1.2rem;padding:1rem;border:3px solid #2a7d3f;border-radius:10px;background:#eaf7ee;font-size:1.2rem}.done h2{margin:.2rem 0 .5rem;font-size:1.6rem}
'''


class AttemptLimiter:
    """Small in-memory limits. They reset on restart, which is fine: the real protection is the
    passkey, this only keeps a flood of requests in check."""

    def __init__(self, clock=time.time) -> None:
        self.clock = clock
        self._posts: Deque[float] = deque()
        self._failures: Dict[str, int] = {}
        self._lock = threading.Lock()

    def allow_post(self) -> bool:
        now = self.clock()
        with self._lock:
            while self._posts and self._posts[0] < now - 60:
                self._posts.popleft()
            if len(self._posts) >= POSTS_PER_MINUTE:
                return False
            self._posts.append(now)
            return True

    def blocked(self, target: str) -> bool:
        with self._lock:
            return self._failures.get(target, 0) >= FAILURES_PER_TARGET

    def failed(self, target: str) -> None:
        with self._lock:
            if len(self._failures) > 10_000:
                self._failures.clear()
            self._failures[target] = self._failures.get(target, 0) + 1


def _secure(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = _CSP
    return response


def _page(title: str, body_html: str, *, kind: str = "", base: str = "") -> Response:
    data = f' data-kind="{html.escape(kind)}" data-base="{html.escape(base, quote=True)}"' if kind else ""
    script = '<script src="/approval/app.js"></script>' if kind else ""
    doc = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f'<title>{html.escape(title)}</title><link rel="stylesheet" href="/approval/style.css"></head>'
        f"<body{data}><h1>{html.escape(title)}</h1>{body_html}{script}</body></html>"
    )
    return _secure(HTMLResponse(doc))


_HELP_HTML = (
    '<div id="help" class="note" hidden>'
    "<p>No passkey prompt? Some embedded windows (for example a browser panel inside an app) cannot "
    "use passkeys. Open this page in your normal browser instead: copy the link and paste it there.</p>"
    '<button id="copy" type="button">Copy link</button> <span id="copied" hidden>Copied.</span></div>'
)


def _allowed_hosts(origin: str) -> frozenset:
    """The Host header values that belong to the configured origin: the exact network location,
    plus the same name with the scheme's default port written out (some proxies add it)."""
    parts = urlsplit(origin)
    netloc = parts.netloc.lower()
    allowed = {netloc}
    if parts.port is None:
        allowed.add(f"{netloc}:{443 if parts.scheme == 'https' else 80}")
    return frozenset(allowed)


def _size(n: int) -> str:
    return f"{n} bytes" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _when(ts: Optional[float]) -> str:
    """A time as UTC text; the page script shows it in the viewer's own time zone."""
    if not ts:
        return "earlier"
    text = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))
    return f'<time data-ts="{int(ts)}">{text}</time>'


def _left(seconds: int) -> str:
    minutes = seconds // 60 + 1
    return f"{minutes} more minutes" if minutes <= 120 else f"{minutes // 60} more hours"


def _send_outcome(result: Dict[str, Any]) -> Dict[str, str]:
    """What the page tells the person after the approval, from the answer of the send step."""
    status = result.get("status")
    to = result.get("to_address") or "the recipient"
    if status == "sent":
        return {"outcome": "sent", "title": "Mail sent",
                "detail": f"Sent to {to}. The mail server accepted it. You can close this page."}
    if status == "sent_but_not_recorded":
        return {"outcome": "unknown", "title": "Mail most likely sent",
                "detail": f"The mail server accepted the message to {to}, but the local status could not "
                          "be stored. Do not send it again."}
    if status == "failed":
        return {"outcome": "failed", "title": "Not sent",
                "detail": "The mail server did not accept the message. Nothing was sent. "
                          "Ask Claude to prepare the mail again."}
    if status == "unknown":
        return {"outcome": "unknown", "title": "Result unclear",
                "detail": "The connection to the mail server broke off. The mail may or may not have "
                          "been sent. Check your sent folder, and do not send it again before you know."}
    note = str(result.get("note") or "Tell Claude that you approved the mail.")
    return {"outcome": "not_sent", "title": "Approved, but not sent", "detail": note}


def _json(ok: bool, message: str = "", status: int = 200, **extra: Any) -> Response:
    return _secure(JSONResponse({"ok": ok, "message": message, **extra}, status_code=status))


def build_approval_routes(
    service: ApprovalService,
    limiter: Optional[AttemptLimiter] = None,
    send: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> List[Route]:
    """`send` is the send step for an approved draft. With it (and `service.config.sends`), the
    page sends the mail itself right after the passkey check."""
    limits = limiter or AttemptLimiter()
    sending = send is not None and service.config.sends
    origin = service.config.origin

    def waits() -> bool:
        """True when send_email waits for the approval: the page then does not ask the person to
        tell the assistant that they approved."""
        return service.config.wait_seconds > 0

    async def read_json(request: Request) -> Optional[Any]:
        if request.headers.get("origin") != origin:
            return None
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return None
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return None
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return None

    async def static_js(request: Request) -> Response:
        return _secure(Response(APP_JS, media_type="text/javascript; charset=utf-8"))

    async def static_css(request: Request) -> Response:
        return _secure(Response(APP_CSS, media_type="text/css; charset=utf-8"))

    def not_found() -> Response:
        r = _page("Not found", "<p>This page does not exist or has expired.</p>")
        r.status_code = 404
        return r

    # -- approving a draft ---------------------------------------------------------

    async def approve_page(request: Request) -> Response:
        draft_id = request.path_params["draft_id"]
        info = service.describe_draft(draft_id)
        if info is None:
            return not_found()
        files = "".join(
            f"<li>{html.escape(a['name'])} ({_size(a['size'])})</li>" for a in info["attachments"]
        ) or "<li>none</li>"
        card = (
            "<dl>"
            f"<dt>To</dt><dd>{html.escape(info['to_name'])} &lt;{html.escape(info['to_address'])}&gt;</dd>"
            f"<dt>Subject</dt><dd>{html.escape(info['subject'])}</dd>"
            f"<dt>Text</dt><dd><pre class=\"text\">{html.escape(info['text'])}</pre></dd>"
            f"<dt>Attachments</dt><dd><ul>{files}</ul></dd>"
            f"<dt>Fingerprint</dt><dd>{html.escape(info['fingerprint'])}</dd>"
            "</dl>"
        )
        state = info["state"]
        lead = ""
        if info["versions"] > 1:
            lead += (
                f'<p class="note">Version {info["version"]} of {info["versions"]} of this mail to '
                f'{html.escape(info["to_address"])}. The other versions stay open until one of them is sent.</p>'
            )
        other = info["other_sent"]
        if other and state in ("open", "approved"):
            lead += (
                '<div class="warn"><strong>Another version of this mail was already sent.</strong> '
                f'To {html.escape(info["to_address"])}, subject &quot;{html.escape(other["subject"])}&quot;, '
                f'{_when(other["at"])}. Send this one as well?</div>'
            )
        card = lead + card
        if state == "open":
            if sending:
                button = "Send this one anyway with passkey" if other else "Approve and send with passkey"
                note = (
                    f'<p class="note">Valid for about {_left(info["seconds_left"])}. '
                    "The mail is sent right after the passkey check, once.</p>"
                )
                done = (
                    '<div id="done" class="done" hidden role="status">'
                    '<h2 id="done-title">Approved</h2><p id="done-text"></p>'
                )
            else:
                button = "Approve with passkey"
                note = (
                    f'<p class="note">Valid for about {_left(info["seconds_left"])}. '
                    "Approving covers exactly this draft, once.</p>"
                )
                done = (
                    '<div id="done" class="done" hidden role="status">'
                    "<h2>Approved</h2>"
                    + (
                        "<p>Claude sends the mail now if it is still waiting, and tells you the result in the chat. "
                        "If Claude already told you that the approval was late, tell it that you approved.</p>"
                        if waits() else
                        "<p>You can go back to Claude now and tell it that you approved.</p>"
                    )
                )
            tail = (
                note + f'<button id="go">{button}</button>'
                '<p id="result" role="status" aria-live="polite"></p>' + _HELP_HTML + done +
                '<button id="close" type="button">Close this page</button>'
                '<p id="closehint" hidden>If the page did not close, close this tab or window yourself '
                "(swipe it away or tap Done), then switch back to Claude.</p></div>"
            )
            return _page("Approve this mail", card + tail, kind="approve", base=f"/approve/{draft_id}")
        sent_at = _when(info["finished_at"])
        messages = {
            "sent": f"This mail was sent to {html.escape(info['to_address'])}, {sent_at}.",
            "unknown": "The result of sending this mail is unclear. Check your sent folder, and do not "
                       "send it again before you know.",
            "failed": "The mail server did not accept this mail. Nothing was sent. Ask Claude to prepare it again.",
            "sending": "This mail is being sent right now. Reload this page in a moment.",
            "approved": (
                "This draft is approved but was not sent. Tell Claude that you approved it."
                if sending else
                "This draft is approved. If Claude is still waiting, it sends the mail by itself; "
                "otherwise tell Claude to send it."
                if waits() else "This draft is approved. Tell Claude to send it."
            ),
            "expired": "This draft has expired. Ask Claude to prepare it again.",
            "closed": "This draft can no longer be approved.",
            "no_passkey": "No passkey is registered yet, so nothing can be approved.",
        }
        page_kind = {"kind": "view", "base": ""} if "<time" in messages[state] or "<time" in card else {}
        return _page("Approve this mail", card + f"<p><strong>{messages[state]}</strong></p>", **page_kind)

    async def approve_options(request: Request) -> Response:
        draft_id = request.path_params["draft_id"]
        body = await read_json(request)
        if body is None or not limits.allow_post():
            return _json(False, "Request refused.", 429 if body is not None else 400)
        if limits.blocked(draft_id):
            return _json(False, "Too many failed attempts for this draft.", 429)
        ok, result = service.approve_options(draft_id)
        if not ok:
            return _json(False, str(result), 400)
        return _secure(JSONResponse({"ok": True, **result}))

    async def approve_verify(request: Request) -> Response:
        draft_id = request.path_params["draft_id"]
        body = await read_json(request)
        if body is None or not limits.allow_post():
            return _json(False, "Request refused.", 429 if body is not None else 400)
        if limits.blocked(draft_id):
            return _json(False, "Too many failed attempts for this draft.", 429)
        ok, message = service.approve_verify(draft_id, body)
        if not ok:
            limits.failed(draft_id)
            return _json(False, message, 400)
        if sending:
            result = await anyio.to_thread.run_sync(send, draft_id)
            outcome = _send_outcome(result)
            return _json(True, f"{outcome['title']}. {outcome['detail']}", **outcome)
        if waits():
            return _json(True, "Approved. If Claude is still waiting, it sends the mail now.")
        return _json(True, "Approved. Go back to Claude and tell it to send the mail.")

    # -- enrolling a passkey -------------------------------------------------------------

    async def enroll_page(request: Request) -> Response:
        token = request.path_params["token"]
        if not service.enrollment_open(token):
            return not_found()
        body = (
            "<p>This registers this device as an approval passkey for the Mail Assistant. "
            "The link works once and for a few minutes.</p>"
            '<button id="go">Register passkey</button>'
            '<p id="result" role="status" aria-live="polite"></p>' + _HELP_HTML
        )
        return _page("Register passkey", body, kind="enroll", base=f"/passkey/enroll/{token}")

    async def enroll_options(request: Request) -> Response:
        token = request.path_params["token"]
        body = await read_json(request)
        if body is None or not limits.allow_post():
            return _json(False, "Request refused.", 429 if body is not None else 400)
        if limits.blocked(token):
            return _json(False, "Too many failed attempts.", 429)
        ok, result = service.enroll_options(token)
        if not ok:
            limits.failed(token)
            return _json(False, str(result), 400)
        return _secure(JSONResponse({"ok": True, **result}))

    async def enroll_verify(request: Request) -> Response:
        token = request.path_params["token"]
        body = await read_json(request)
        if body is None or not limits.allow_post():
            return _json(False, "Request refused.", 429 if body is not None else 400)
        if limits.blocked(token):
            return _json(False, "Too many failed attempts.", 429)
        ok, message = service.enroll_verify(token, body)
        if not ok:
            limits.failed(token)
            return _json(False, message, 400)
        return _json(True, "Passkey registered. You can close this page.")

    allowed_hosts = _allowed_hosts(origin)

    def guarded(endpoint):
        """Every approval route answers only under the configured host name. This closes DNS
        rebinding: a page on another name that resolves to this server is refused, and so is a
        request that carries zero or several Host headers."""

        async def wrapper(request: Request) -> Response:
            hosts = request.headers.getlist("host")
            if len(hosts) != 1 or hosts[0].strip().lower() not in allowed_hosts:
                return _secure(Response("Misdirected request.", status_code=421, media_type="text/plain"))
            return await endpoint(request)

        return wrapper

    return [
        Route("/approval/app.js", guarded(static_js), methods=["GET"]),
        Route("/approval/style.css", guarded(static_css), methods=["GET"]),
        Route("/approve/{draft_id}", guarded(approve_page), methods=["GET"]),
        Route("/approve/{draft_id}/options", guarded(approve_options), methods=["POST"]),
        Route("/approve/{draft_id}/verify", guarded(approve_verify), methods=["POST"]),
        Route("/passkey/enroll/{token}", guarded(enroll_page), methods=["GET"]),
        Route("/passkey/enroll/{token}/options", guarded(enroll_options), methods=["POST"]),
        Route("/passkey/enroll/{token}/verify", guarded(enroll_verify), methods=["POST"]),
    ]
