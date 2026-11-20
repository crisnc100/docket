import hmac
import json
import secrets
from collections.abc import Callable
from pathlib import Path

from starlette.requests import cookie_parser
from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from docket.beta import SESSION_COOKIE, header_values, same_origin, valid_session
from docket.settings import Settings

WEB = Path(__file__).resolve().parent.parent / "web"


ACCESS_FORMS = {
    "token": {"__LABEL__": "Reviewer token", "__BUTTON__": "Unlock review"},
    "invite": {"__LABEL__": "Invite code", "__BUTTON__": "Sign in"},
}
BETA_MAX_BODY = 16 * 1024
# Visitors never send their own documents to the beta; these are refused before any body is read.
BETA_CLOSED_WRITES = {("POST", "/documents"), ("POST", "/inbound")}


def reviewer_page(status_code: int = 200, access: str = "token"):
    assets = WEB / "dist" / "assets"
    if not assets.is_dir():
        return PlainTextResponse("Build the review page with npm --prefix web run build", 503)
    nonce = secrets.token_urlsafe(24)
    paths = {
        kind: ["/assets/" + item.name for item in sorted(assets.glob("*." + suffix))]
        for kind, suffix in [("scripts", "js"), ("styles", "css")]
    }
    html = (WEB / "login.html").read_text().replace("__NONCE__", nonce)
    html = html.replace("__ASSETS__", json.dumps(paths).replace("<", "\\u003c"))
    for placeholder, text in ACCESS_FORMS[access].items():
        html = html.replace(placeholder, text)
    # A 200 in invite mode means the session cookie was valid, so the page opens the app.
    signed_in = access == "invite" and status_code == 200
    html = html.replace("__ACCESS__", json.dumps(access)).replace(
        "__SIGNED_IN__", json.dumps(signed_in)
    )
    return HTMLResponse(
        html,
        status_code,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}' blob:; "
                f"style-src 'nonce-{nonce}' blob:; font-src 'self'; connect-src 'self'; "
                "frame-src blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


def public(method: str, path: str) -> bool:
    """Routes that need no reviewer token: the webhook, the health check, the front page at
    exactly "/" and its static files under /home/."""
    if (method, path) in {("POST", "/inbound"), ("GET", "/healthz"), ("GET", "/")}:
        return True
    if method != "GET" or not path.startswith("/home/") or "\\" in path:
        return False
    # ASGI paths arrive percent-decoded but not dot-normalised, so /home/../review must not
    # count as a front-page file.
    return all(part not in {".", ".."} for part in path.split("/"))


def beta_public(method: str, path: str) -> bool:
    """The beta's only routes without a session: the front page and its files, the health
    check and the sign-in itself. The webhook is not public in beta."""
    if (method, path) == ("POST", "/reviewer/session"):
        return True
    return method == "GET" and public(method, path)


def has_session(scope: Scope, settings: Settings) -> bool:
    cookies = cookie_parser("; ".join(header_values(scope["headers"], b"cookie")))
    value = cookies.get(SESSION_COOKIE)
    return value is not None and valid_session(settings, value)


def refuse(status: int, text: str) -> PlainTextResponse:
    return PlainTextResponse(text, status, headers={"Cache-Control": "no-store"})


async def buffered_body(scope: Scope, receive: Receive, limit: int) -> Receive | PlainTextResponse:
    """Read the whole body before any handler runs, refusing it once it passes the limit."""
    try:
        length = int(dict(scope["headers"]).get(b"content-length", b"0"))
    except ValueError:
        return refuse(400, "Invalid Content-Length.")
    if length > limit:
        return refuse(413, f"Request bodies are limited to {limit // 1024} KB.")
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return refuse(400, "The client disconnected.")
        body.extend(message.get("body", b""))
        if len(body) > limit:
            return refuse(413, f"Request bodies are limited to {limit // 1024} KB.")
        if not message.get("more_body", False):
            break
    replayed = False

    # The body goes to the app once; after that, receive waits for the disconnect as usual.
    async def replay() -> Message:
        nonlocal replayed
        if replayed:
            return await receive()
        replayed = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    return replay


async def no_store(app: ASGIApp, scope: Scope, receive: Receive, send: Send):
    async def private_send(message):
        if message["type"] == "http.response.start":
            message["headers"] = [
                (key, value) for key, value in message["headers"] if key.lower() != b"cache-control"
            ] + [(b"cache-control", b"no-store")]
        await send(message)

    await app(scope, receive, private_send)


class ReviewerAccess:
    def __init__(self, app: ASGIApp, settings: Callable[[], Settings]):
        self.app, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and self.settings().docket_env == "beta":
            return await self.beta(scope, receive, send, self.settings())
        if scope["type"] != "http" or public(scope["method"], scope["path"]):
            return await self.app(scope, receive, send)
        configured = self.settings().docket_review_token
        expected = configured.get_secret_value() if configured is not None else ""
        if not expected or expected.isspace():
            return await PlainTextResponse(
                "Reviewer access is disabled: set DOCKET_REVIEW_TOKEN.",
                503,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
        headers = dict(scope["headers"])
        scheme, _, provided = headers.get(b"authorization", b"").partition(b" ")
        valid = hmac.compare_digest(provided, expected.encode())
        if scheme.lower() != b"bearer" or not valid:
            response = (
                reviewer_page(401)
                if (scope["method"], scope["path"]) == ("GET", "/reviewer")
                else PlainTextResponse(
                    "A valid reviewer Bearer token is required.",
                    401,
                    headers={"Cache-Control": "no-store"},
                )
            )
            response.headers["WWW-Authenticate"] = "Bearer"
            return await response(scope, receive, send)

        await no_store(self.app, scope, receive, send)

    async def beta(self, scope: Scope, receive: Receive, send: Send, settings: Settings):
        """Beta access is the session cookie alone: a Bearer token grants nothing, whether or
        not one is configured."""
        method, path = scope["method"], scope["path"]
        # The router's patterns end in "$", which also matches before a trailing line feed, so
        # "/documents\n" would reach the upload handler. No real path has a control character.
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in path):
            return await refuse(400, "Invalid path.")(scope, receive, send)
        open_route = beta_public(method, path)
        if not open_route and not has_session(scope, settings):
            if (method, path) == ("GET", "/reviewer"):
                return await reviewer_page(401, "invite")(scope, receive, send)
            return await refuse(401, "Sign in with an invite code first.")(scope, receive, send)
        if method != "GET" and not same_origin(scope, settings):
            return await refuse(403, "Cross-site requests are refused.")(scope, receive, send)
        # "/documents/" and "//documents" never reach the handler, but refusing them here too
        # means their bodies are never read either.
        if (method, "/" + "/".join(part for part in path.split("/") if part)) in BETA_CLOSED_WRITES:
            return await refuse(403, "The hosted beta does not accept documents.")(
                scope, receive, send
            )
        replay = await buffered_body(scope, receive, BETA_MAX_BODY)
        if isinstance(replay, PlainTextResponse):
            return await replay(scope, receive, send)
        if open_route:
            return await self.app(scope, replay, send)
        await no_store(self.app, scope, replay, send)
