"""The front page at "/" is public; the review page and every other route need the token."""

import asyncio
from html.parser import HTMLParser
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from docket.api import serve_home
from docket.review_access import ReviewerAccess, public
from docket.settings import Settings

HOME = Path("web/public/home")


def guarded_app():
    app = FastAPI()
    serve_home(app, HOME)

    @app.get("/review")
    def queue():
        return {"items": []}

    @app.get("/reviewer")
    def reviewer():
        return {"page": "review"}

    return ReviewerAccess(app, lambda: Settings(docket_review_token=SecretStr("secret")))


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def send():
        transport = httpx.ASGITransport(guarded_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def raw_status(path: str) -> int:
    """Send a path exactly as given; httpx would normalise dot segments before sending."""
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    asyncio.run(guarded_app()(scope, receive, send))
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


class _Headline(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.parts = 0, []

    def handle_starttag(self, tag, attrs):
        if tag == "h1" or self.depth:
            self.depth += 1

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def headline(html: str) -> str:
    parser = _Headline()
    parser.feed(html)
    return " ".join("".join(parser.parts).replace("\u00a0", " ").split())


def test_front_page_is_served_at_root_without_a_token():
    response = request("GET", "/")
    assert response.status_code == 200
    assert headline(response.text) == (
        "Property documents filed to the right unit, or held for a person."
    )
    assert response.headers["content-type"].startswith("text/html")
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_front_page_files_are_public():
    for path in (
        "/home/demo.json",
        "/home/home.js",
        "/home/home.css",
        "/home/fonts/geist-latin.woff2",
    ):
        assert request("GET", path).status_code == 200, path


def test_the_page_has_one_address():
    for path in ("/home/", "/home/index.html"):
        response = request("GET", path)
        assert response.status_code == 308, path
        assert response.headers["location"] == "/", path


def test_front_page_never_serves_files_outside_its_folder():
    assert request("GET", "/home/missing.html").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/reviewer",
        "/review",
        "/home",
        "/homepage",
        "/home/../review",
        "/home/../reviewer",
        "/home/./../review",
        "/home/..",
        "/home/..\\review",
        "//",
    ],
)
def test_paths_that_only_look_public_still_need_the_token(path):
    assert raw_status(path) == 401


def test_only_get_is_public_on_the_front_page():
    assert request("POST", "/").status_code == 401
    assert request("HEAD", "/").status_code == 401
    assert request("POST", "/home/demo.json").status_code == 401


def test_reviewer_routes_work_with_the_token():
    headers = {"Authorization": "Bearer secret"}
    assert request("GET", "/review", headers=headers).status_code == 200
    assert request("GET", "/reviewer", headers=headers).status_code == 200


def test_public_paths_are_exactly_the_front_page_its_files_and_the_two_service_routes():
    assert public("GET", "/")
    assert public("GET", "/home/home.css")
    assert public("POST", "/inbound") and public("GET", "/healthz")
    assert not public("GET", "/reviewer")
    assert not public("GET", "/home")
    assert not public("GET", "/home/../review")
    assert not public("HEAD", "/home/home.css")
    assert not public("POST", "/")
