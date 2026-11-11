import hashlib
import hmac
import time

import pytest

from docket.webhook import valid_signature


@pytest.mark.parametrize(
    "timestamp",
    [
        "9" * 310,
        "9" * 4300,
        "9" * 4301,
        "+{now}",
        " {now}",
        "{now} ",
        "{now}.0",
        "1_000",
        "١٧٩٤٤١٠٠٠٠",
    ],
)
def test_nonplain_or_out_of_range_timestamp_is_invalid_without_overflow(timestamp):
    timestamp = timestamp.format(now=int(time.time()))
    body = b"{}"
    key = b"test-fixture"
    digest = hmac.new(key, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert valid_signature(key.decode(), timestamp, "sha256=" + digest, body) is False
