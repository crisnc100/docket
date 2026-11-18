"""The front page is public; every reviewer route still needs the token."""

import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from docket.api import serve_home
from docket.review_access import ReviewerAccess, public
from docket.settings import Settings

HOME = Path("web/public/home")


def request(method: str, path: str, **kwargs) -> httpx.Response:
    app = FastAPI()
    serve_home(app, HOME)

    @app.get("/review")
    def queue():
        return {"items": []}

    guarded = ReviewerAccess(app, lambda: Settings(docket_review_token=SecretStr("secret")))

    async def send():
        transport = httpx.ASGITransport(guarded)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_front_page_needs_no_token_and_carries_its_own_headers():
    response = request("GET", "/home/")
    assert response.status_code == 200
    assert "Property documents filed to the right unit" in response.text
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


def test_bare_home_redirects_to_the_page():
    response = request("GET", "/home")
    assert response.status_code == 308
    assert response.headers["location"] == "/home/"


def test_front_page_never_serves_files_outside_its_folder():
    assert request("GET", "/home/missing.html").status_code == 404
    assert request("GET", "/home/%2e%2e/login.html").status_code == 404
    assert request("GET", "/home/..%2f..%2fpyproject.toml").status_code == 404


def test_reviewer_routes_still_need_the_token():
    assert request("GET", "/review").status_code == 401
    assert request("POST", "/home/").status_code == 401
    assert request("GET", "/homepage").status_code == 401
    authorized = request("GET", "/review", headers={"Authorization": "Bearer secret"})
    assert authorized.status_code == 200


def test_only_reads_under_home_are_public():
    assert public("GET", "/home/")
    assert public("HEAD", "/home/home.css")
    assert not public("POST", "/home/")
    assert not public("GET", "/homepage")
    assert not public("GET", "/")
    assert public("POST", "/inbound") and public("GET", "/healthz")
