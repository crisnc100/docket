import hashlib
import hmac
import time
from collections import deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MAX_BODY = 10 * 1024 * 1024


class BodyLimit:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["path"] not in {"/inbound", "/documents"}:
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(
                scope, receive, send
            )
        if length > MAX_BODY:
            return await JSONResponse({"detail": "Body exceeds 10 MB"}, 413)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_BODY:
                return await JSONResponse({"detail": "Body exceeds 10 MB"}, 413)(
                    scope, receive, send
                )
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        async def replay():
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


class RateLimit:
    def __init__(self, limit: int, window: float = 60):
        self.limit, self.window = limit, window
        self.requests: dict[str, deque[float]] = {}

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        # Expire inactive peers too, so arbitrary client addresses cannot grow state forever.
        for peer in list(self.requests):
            queue = self.requests[peer]
            while queue and queue[0] <= now - self.window:
                queue.popleft()
            if not queue:
                del self.requests[peer]
        queue = self.requests.setdefault(ip, deque())
        if len(queue) >= self.limit:
            return False
        queue.append(now)
        return True


def valid_signature(secret: str, timestamp: str, signature: str, body: bytes) -> bool:
    if not timestamp or len(timestamp) > 19 or not timestamp.isascii() or not timestamp.isdecimal():
        return False
    try:
        value = int(timestamp)
        fresh = value <= 2**63 - 1 and abs(time.time() - value) <= 300
    except (ValueError, OverflowError):
        return False
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return (
        bool(secret)
        and fresh
        and hmac.compare_digest(("sha256=" + expected).encode(), signature.encode())
    )
