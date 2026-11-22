"""The hosted beta's gate: startup refusals, the client's address and the Origin check."""

import hashlib
import hmac
import ipaddress
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from docket.settings import Settings

MIN_ADDRESS_KEY_BYTES = 32
MAX_DAILY_BUDGET_USD = 1.00
# A held document's workflow ends on its own by then, close to when its sandbox does.
MAX_REVIEW_TIMEOUT_SECONDS = 3600


class UnsafeConfig(SystemExit):
    """Raised before a beta process does any work, so it exits with one plain message."""


def startup_problems(settings: Settings, environ: Mapping[str, str]) -> list[str]:
    """Every reason a beta process must not start. Messages never include a secret value."""
    if settings.docket_env != "beta":
        return []
    problems = []
    if len(settings.docket_address_key.get_secret_value().encode()) < MIN_ADDRESS_KEY_BYTES:
        problems.append(f"DOCKET_ADDRESS_KEY needs at least {MIN_ADDRESS_KEY_BYTES} bytes.")
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
    if settings.docket_review_timeout_seconds > MAX_REVIEW_TIMEOUT_SECONDS:
        problems.append(
            f"DOCKET_REVIEW_TIMEOUT_SECONDS must be at most {MAX_REVIEW_TIMEOUT_SECONDS} in beta."
        )
    if environ.get("UVICORN_ROOT_PATH"):
        problems.append("UVICORN_ROOT_PATH must be unset: the beta is served at the site root.")
    return problems


def refuse_unsafe_start(settings: Settings, environ: Mapping[str, str] | None = None) -> None:
    """Called first by every process that starts: the API, the worker, reconcile, eval and
    spike-models. It does nothing outside beta."""
    problems = startup_problems(settings, os.environ if environ is None else environ)
    if problems:
        raise UnsafeConfig("Docket beta refuses to start:\n- " + "\n- ".join(problems))


def header_values(headers: list[tuple[bytes, bytes]], name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in headers if key.lower() == name]


def client_address(scope: Mapping, settings: Settings) -> str:
    """The address the limits count. An IPv6 address counts by its /64, since one host is
    commonly given a whole /64 and could otherwise step through it."""
    address = None
    if settings.docket_trust_proxy:
        # The proxy appends the address it saw, so only the last hop is trustworthy; a client
        # can write anything before it.
        hops = ",".join(header_values(scope["headers"], b"x-forwarded-for")).split(",")
        address = hops[-1].strip() or None
    if address is None:
        client = scope.get("client")
        address = client[0] if client else "unknown"
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.IPv6Network((parsed, 64), strict=False))
    return str(parsed)


def address_hash(settings: Settings, address: str) -> str:
    """What the counters store instead of the address: an HMAC under a key the database never
    sees, so a dump of it names no visitor."""
    key = settings.docket_address_key.get_secret_value().encode()
    return hmac.new(key, address.encode(), hashlib.sha256).hexdigest()


def same_origin(scope: Mapping, settings: Settings) -> bool:
    base = urlsplit(settings.docket_base_url.strip())
    if not base.scheme or not base.netloc:
        return False
    origins = header_values(scope["headers"], b"origin")
    return len(origins) == 1 and origins[0].lower() == f"{base.scheme}://{base.netloc}".lower()
