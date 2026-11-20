"""The hosted beta's gate: startup refusals, invite-code sessions and the wrong-code lockout."""

import hashlib
import hmac
import math
import os
import time
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.orm import Session

from docket.db import SigninFailure
from docket.settings import Settings

EXAMPLE_ENV = Path(__file__).resolve().parent.parent / ".env.beta.example"
SESSION_COOKIE = "__Host-docket_session"
SESSION_MAX_AGE = 7 * 24 * 3600
# A cookie issued by this server is never dated ahead of its clock; the slack covers
# processes whose clocks differ by a little.
CLOCK_SLACK = 60
MIN_CODE_LENGTH = 16
MIN_SESSION_KEY_BYTES = 32
MAX_DAILY_BUDGET_USD = 1.00
WRONG_CODE_LIMIT = 10
WRONG_CODE_WINDOW = 3600


class UnsafeConfig(SystemExit):
    """Raised before a beta process does any work, so it exits with one plain message."""


def invite_codes(settings: Settings) -> list[str]:
    return [code.strip() for code in settings.docket_invite_codes.get_secret_value().split(",")]


def startup_problems(
    settings: Settings, environ: Mapping[str, str], example: Path | None = None
) -> list[str]:
    """Every reason a beta process must not start. Messages never include a secret value."""
    if settings.docket_env != "beta":
        return []
    problems = []
    codes = invite_codes(settings)
    example = example or EXAMPLE_ENV
    placeholders = example.read_text() if example.is_file() else ""
    if not settings.docket_invite_codes.get_secret_value().strip():
        problems.append("DOCKET_INVITE_CODES is not set.")
    elif any(not code for code in codes):
        problems.append("DOCKET_INVITE_CODES has a blank entry.")
    else:
        if any(len(code) < MIN_CODE_LENGTH for code in codes):
            problems.append(f"Each invite code needs at least {MIN_CODE_LENGTH} characters.")
        if any(code in placeholders for code in codes):
            problems.append("An invite code is a placeholder from .env.beta.example.")
    if len(settings.docket_session_key.get_secret_value().encode()) < MIN_SESSION_KEY_BYTES:
        problems.append(f"DOCKET_SESSION_KEY needs at least {MIN_SESSION_KEY_BYTES} bytes.")
    token = settings.docket_review_token
    # A blank token grants nothing, and the local compose file passes one by default.
    if token is not None and token.get_secret_value().strip():
        problems.append("DOCKET_REVIEW_TOKEN must be unset in beta.")
    if not settings.docket_trust_proxy:
        problems.append("DOCKET_TRUST_PROXY=1 is required in beta.")
    failpoints = sorted(name for name in environ if name.startswith("DOCKET_FAILPOINT"))
    if failpoints:
        problems.append("Failpoint variables must be unset in beta: " + ", ".join(failpoints))
    if "docket_daily_budget_usd" not in settings.model_fields_set:
        problems.append("DOCKET_DAILY_BUDGET_USD must be set in beta.")
    elif not settings.docket_daily_budget_usd <= MAX_DAILY_BUDGET_USD:
        problems.append(f"DOCKET_DAILY_BUDGET_USD must be at most {MAX_DAILY_BUDGET_USD:.2f}.")
    if settings.docket_model_mode not in {"live", "replay"}:
        problems.append("DOCKET_MODEL_MODE must be live or replay in beta.")
    return problems


def refuse_unsafe_start(
    settings: Settings, environ: Mapping[str, str] | None = None, example: Path | None = None
) -> None:
    """Called first by every process that starts: the API, the worker, reconcile, eval and
    spike-models. It does nothing outside beta."""
    problems = startup_problems(settings, os.environ if environ is None else environ, example)
    if problems:
        raise UnsafeConfig("Docket beta refuses to start:\n- " + "\n- ".join(problems))


def _signature(settings: Settings, issued: str, code: str) -> str:
    # The code's hash stands in for the code, so the cookie never carries the code itself.
    code_id = hashlib.sha256(code.encode()).hexdigest()
    key = settings.docket_session_key.get_secret_value().encode()
    return hmac.new(key, f"{issued}.{code_id}".encode(), hashlib.sha256).hexdigest()


def session_value(settings: Settings, code: str, now: float | None = None) -> str:
    issued = str(int(time.time() if now is None else now))
    return f"{issued}.{_signature(settings, issued, code)}"


def valid_session(settings: Settings, value: str, now: float | None = None) -> bool:
    issued, _, signature = value.partition(".")
    if not issued.isascii() or not issued.isdecimal() or len(issued) > 12:
        return False
    age = (time.time() if now is None else now) - int(issued)
    if not -CLOCK_SLACK <= age <= SESSION_MAX_AGE:
        return False
    # Checked against the codes configured now, so removing a code ends its sessions.
    return any(
        hmac.compare_digest(signature, _signature(settings, issued, code))
        for code in invite_codes(settings)
        if code
    )


def session_cookie(value: str) -> str:
    return f"{SESSION_COOKIE}={value}; HttpOnly; Secure; SameSite=Strict; Path=/"


def known_code(settings: Settings, code: str) -> bool:
    return any(
        hmac.compare_digest(code.encode(), known.encode())
        for known in invite_codes(settings)
        if known
    )


def header_values(headers: list[tuple[bytes, bytes]], name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in headers if key.lower() == name]


def client_address(scope: Mapping, settings: Settings) -> str:
    if settings.docket_trust_proxy:
        # The proxy appends the address it saw, so only the last hop is trustworthy; a client
        # can write anything before it.
        hops = ",".join(header_values(scope["headers"], b"x-forwarded-for")).split(",")
        if hops[-1].strip():
            return hops[-1].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def same_origin(scope: Mapping, settings: Settings) -> bool:
    base = urlsplit(settings.docket_base_url.strip())
    if not base.scheme or not base.netloc:
        return False
    origins = header_values(scope["headers"], b"origin")
    return len(origins) == 1 and origins[0].lower() == f"{base.scheme}://{base.netloc}".lower()


def _address_lock(address: str) -> int:
    return int.from_bytes(hashlib.sha256(b"signin:" + address.encode()).digest()[:8], signed=True)


def reserve_attempt(engine: Engine, address: str) -> int | None:
    """Count one sign-in attempt for this address before the code is checked, and return its
    id. None means the address already has ten attempts in the last hour. Counting first, under
    a per-address lock, keeps parallel guesses inside the limit; a right code gives its attempt
    back with release_attempt."""
    window_start = func.now() - timedelta(seconds=WRONG_CODE_WINDOW)
    with Session(engine) as session, session.begin():
        session.execute(select(func.pg_advisory_xact_lock(_address_lock(address))))
        session.execute(
            delete(SigninFailure).where(
                SigninFailure.address == address, SigninFailure.at <= window_start
            )
        )
        recent = session.scalar(select(func.count()).where(SigninFailure.address == address))
        if recent is not None and recent >= WRONG_CODE_LIMIT:
            return None
        return session.execute(
            insert(SigninFailure).values(address=address).returning(SigninFailure.id)
        ).scalar_one()


def retry_after(engine: Engine, address: str) -> int:
    """Seconds until the address's oldest counted attempt leaves the window."""
    with Session(engine) as session:
        oldest = session.scalar(
            select(func.min(SigninFailure.at)).where(SigninFailure.address == address)
        )
        now = session.scalar(select(func.now()))
    if oldest is None or now is None:
        return 1
    remaining = (oldest - now).total_seconds() + WRONG_CODE_WINDOW
    return max(1, math.ceil(remaining))


def release_attempt(engine: Engine, attempt: int) -> None:
    with Session(engine) as session, session.begin():
        session.execute(delete(SigninFailure).where(SigninFailure.id == attempt))
