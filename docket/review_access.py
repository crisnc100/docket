import hmac
import json
import re
import secrets
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path

from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.routing import BaseRoute, Match, Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from docket.beta import same_origin
from docket.settings import Settings

WEB = Path(__file__).resolve().parent.parent / "web"


FORM = re.compile(r"<!--form-->.*?<!--/form-->", re.DOTALL)
BETA_MAX_BODY = 16 * 1024
# Visitors never send their own documents to the beta; these are refused before any body is read.
BETA_CLOSED_WRITES = {("POST", "/documents"), ("POST", "/inbound")}


class Access(StrEnum):
    PUBLIC = "public"
    SANDBOX = "sandbox"
    CLOSED = "closed"
    REFUSED = "refused"


# Every route the app serves, by method and template. The beta answers only what is listed
# as public or sandbox-scoped; a sandbox-scoped route shows each visitor their own sandbox
# and nothing else. tests/test_sandbox_routes.py fails when a route is missing here.
BETA_ROUTES: dict[tuple[str, str], Access] = {
    ("GET", "/"): Access.PUBLIC,
    ("GET", "/home/{path:path}"): Access.PUBLIC,
    ("GET", "/healthz"): Access.PUBLIC,
    ("GET", "/reviewer"): Access.PUBLIC,
    ("GET", "/assets/{path:path}"): Access.PUBLIC,
    ("GET", "/review"): Access.SANDBOX,
    ("POST", "/review/{document_id}/{action}"): Access.SANDBOX,
    ("GET", "/documents/{document_id}"): Access.SANDBOX,
    ("GET", "/documents/{document_id}/file"): Access.SANDBOX,
    ("GET", "/beta/samples"): Access.SANDBOX,
    ("POST", "/beta/samples"): Access.SANDBOX,
    ("GET", "/sandbox"): Access.SANDBOX,
    ("DELETE", "/sandbox"): Access.SANDBOX,
    ("POST", "/documents"): Access.CLOSED,
    ("POST", "/inbound"): Access.CLOSED,
    **{
        (method, path): Access.REFUSED
        for method in ("GET", "HEAD")
        for path in ("/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc")
    },
}


def route_template(routes: Sequence[BaseRoute], scope: Scope) -> str | None:
    """The template of the route the router would run for this request, found by the
    router's own matching, so the gate and the router can never disagree about a path."""
    for route in routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            path = getattr(route, "path", None)
            if isinstance(route, Mount):
                return f"{path}/{{path:path}}"
            return path
    return None


def beta_access(routes: Sequence[BaseRoute], scope: Scope) -> Access:
    template = route_template(routes, scope)
    if template is None:
        return Access.REFUSED
    return BETA_ROUTES.get((scope["method"], template), Access.REFUSED)


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
    # "sandbox" opens the review app at once, so it has no form to fill in.
    if access == "sandbox":
        html = FORM.sub("", html)
    html = html.replace("__ACCESS__", json.dumps(access))
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
    def __init__(
        self,
        app: ASGIApp,
        settings: Callable[[], Settings],
        # Without the app's routes, nothing in beta matches the table, so it is all refused.
        routes: Callable[[], Sequence[BaseRoute]] = lambda: (),
    ):
        self.app, self.settings, self.routes = app, settings, routes

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
        """Beta access is by route alone: each sandbox-scoped route shows the visitor their
        own sandbox, named by its cookie, and nothing else. A Bearer token grants nothing,
        whether or not one is configured."""
        method, path = scope["method"], scope["path"]
        # Under a root path the router matches the path without it while this gate reads the
        # full path, so a private route could pass as a public one.
        if scope.get("root_path"):
            return await refuse(500, "The beta must be served at the site root.")(
                scope, receive, send
            )
        # The router's patterns end in "$", which also matches before a trailing line feed, so
        # "/documents\n" would reach the upload handler. No real path has a control character.
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in path):
            return await refuse(400, "Invalid path.")(scope, receive, send)
        access = beta_access(self.routes(), scope)
        # "/documents/" and "//documents" never reach the handler, but refusing them here too
        # means their bodies are never read either.
        normal = "/" + "/".join(part for part in path.split("/") if part)
        if access == Access.CLOSED or (method, normal) in BETA_CLOSED_WRITES:
            return await refuse(403, "The hosted beta does not accept documents.")(
                scope, receive, send
            )
        if access == Access.REFUSED:
            return await refuse(404, "Not found.")(scope, receive, send)
        if method != "GET" and not same_origin(scope, settings):
            return await refuse(403, "Cross-site requests are refused.")(scope, receive, send)
        replay = await buffered_body(scope, receive, BETA_MAX_BODY)
        if isinstance(replay, PlainTextResponse):
            return await replay(scope, receive, send)
        if access == Access.PUBLIC:
            return await self.app(scope, replay, send)
        await no_store(self.app, scope, replay, send)
