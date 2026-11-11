import hmac
import json
import secrets
from collections.abc import Callable
from pathlib import Path

from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from docket.settings import Settings

WEB = Path(__file__).resolve().parent.parent / "web"


def reviewer_page(status_code: int = 200):
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
    return HTMLResponse(
        html,
        status_code,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}' blob:; "
                f"style-src 'nonce-{nonce}' blob:; connect-src 'self'; "
                "frame-src blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


class ReviewerAccess:
    def __init__(self, app: ASGIApp, settings: Callable[[], Settings]):
        self.app, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or (scope["method"], scope["path"]) in {
            ("POST", "/inbound"),
            ("GET", "/healthz"),
        }:
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
                if (scope["method"], scope["path"]) == ("GET", "/")
                else PlainTextResponse(
                    "A valid reviewer Bearer token is required.",
                    401,
                    headers={"Cache-Control": "no-store"},
                )
            )
            response.headers["WWW-Authenticate"] = "Bearer"
            return await response(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (key, value)
                    for key, value in message["headers"]
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, receive, private_send)
