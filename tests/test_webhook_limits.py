import pytest

from docket.webhook import MAX_BODY, BodyLimit


@pytest.mark.parametrize("path", ["/inbound", "/documents"])
@pytest.mark.parametrize("declared", [False, True])
async def test_limit_rejects_without_reading_remainder(path, declared):
    reads = 0
    sent = []

    async def app(scope, receive, send):
        raise AssertionError("Oversize request reached application")

    async def receive():
        nonlocal reads
        reads += 1
        assert reads <= 2, "Remainder of body was consumed"
        return {"type": "http.request", "body": b"x" * (MAX_BODY // 2 + 1), "more_body": True}

    async def send(message):
        sent.append(message)

    await BodyLimit(app)(
        {
            "type": "http",
            "path": path,
            "headers": [(b"content-length", str(MAX_BODY + 1).encode())] if declared else [],
        },
        receive,
        send,
    )
    assert sent[0]["status"] == 413
    assert reads == (0 if declared else 2)
