import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

import boto3
import pytest
from botocore.client import Config
from botocore.exceptions import ClientError

from docket.config import Category, load_records
from docket.core.storage import (
    InvalidContentHash,
    InvalidStorageKey,
    S3Storage,
    StorageConflict,
    build_key,
)


@pytest.fixture()
def s3():
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://127.0.0.1:9000"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY", "docket-local"),
        aws_secret_access_key=os.getenv("S3_SECRET_KEY", "docket-local-password"),
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    bucket = "p2-test-" + uuid.uuid4().hex
    client.create_bucket(Bucket=bucket)
    try:
        yield client, bucket, S3Storage(client, bucket)
    finally:
        objects = client.list_objects_v2(Bucket=bucket).get("Contents", [])
        for item in objects:
            client.delete_object(Bucket=bucket, Key=item["Key"])
        client.delete_bucket(Bucket=bucket)
        client.close()


def sha(content):
    return hashlib.sha256(content).hexdigest()


def test_key_uses_only_record_config_category_and_hash():
    record = load_records()[0]
    content_hash = sha(b"pdf")
    assert build_key(record, Category.LEASE, content_hash) == (
        f"CEDAR_01/UNIT-0001/lease/{content_hash[:16]}.pdf"
    )
    with pytest.raises(InvalidStorageKey):
        build_key(record, "../lease", content_hash)
    unsafe = record.model_copy(update={"building": "../escape"})
    with pytest.raises(InvalidStorageKey):
        build_key(unsafe, Category.LEASE, content_hash)


def test_real_s3_put_retry_is_noop_and_filename_is_metadata(s3):
    client, bucket, storage = s3
    content = b"%PDF synthetic fixture"
    key = build_key(load_records()[0], Category.LEASE, sha(content))
    filename = "../../contrat évil.pdf"
    assert storage.put_if_absent(key, content, sha(content), original_filename=filename) is True
    first = client.head_object(Bucket=bucket, Key=key)
    assert (
        storage.put_if_absent(key, content, sha(content), original_filename="changed.pdf") is False
    )
    second = client.head_object(Bucket=bucket, Key=key)
    assert first["ETag"] == second["ETag"]
    assert first["LastModified"] == second["LastModified"]
    assert first["Metadata"] == second["Metadata"]
    assert unquote(first["Metadata"]["original-filename"]) == filename
    assert first["Metadata"]["sha256"] == sha(content)
    assert client.get_object(Bucket=bucket, Key=key)["Body"].read() == content
    assert client.list_objects_v2(Bucket=bucket)["KeyCount"] == 1


def test_real_s3_conflict_preserves_original_object(s3):
    client, bucket, storage = s3
    first, other = b"first", b"different"
    assert storage.put_if_absent("conflict.pdf", first, sha(first))
    with pytest.raises(StorageConflict) as error:
        storage.put_if_absent("conflict.pdf", other, sha(other))
    assert error.value.status_reason == "storage_conflict" and error.value.non_retryable
    assert client.get_object(Bucket=bucket, Key="conflict.pdf")["Body"].read() == first


def test_real_s3_concurrent_puts_have_one_creator(s3):
    client, bucket, storage = s3
    content = b"same concurrent bytes"
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(
            executor.map(
                lambda _: storage.put_if_absent("concurrent.pdf", content, sha(content)), range(6)
            )
        )
    assert sum(results) == 1
    assert client.list_objects_v2(Bucket=bucket)["KeyCount"] == 1


def test_real_s3_checks_content_not_claimed_metadata(s3):
    client, bucket, storage = s3
    content = b"wanted"
    client.put_object(
        Bucket=bucket, Key="untrusted.pdf", Body=b"other", Metadata={"sha256": sha(content)}
    )
    with pytest.raises(StorageConflict):
        storage.put_if_absent("untrusted.pdf", content, sha(content))


@pytest.mark.parametrize("content", [b"", b"x" * (1024 * 1024 + 1)])
def test_real_s3_hashes_empty_and_multichunk_objects(s3, content):
    _, _, storage = s3
    assert storage.put_if_absent("chunks.pdf", content, sha(content))
    assert storage.put_if_absent("chunks.pdf", content, sha(content)) is False


@pytest.mark.parametrize(
    "key",
    [
        "../escape",
        "/absolute",
        "a//b",
        "a/../b",
        "a\\b",
        "",
        "a/%2e%2e/b",
        "a/./b",
        "a/",
        "a/évil.pdf",
    ],
)
def test_key_traversal_rejected_before_storage(s3, key):
    client, bucket, storage = s3
    with pytest.raises(InvalidStorageKey):
        storage.put_if_absent(key, b"x", sha(b"x"))
    assert client.list_objects_v2(Bucket=bucket)["KeyCount"] == 0


@pytest.mark.parametrize("digest", ["x" * 64, "a" * 63, "A" * 64, "../a", ""])
def test_invalid_hash_is_rejected(s3, digest):
    _, _, storage = s3
    with pytest.raises(InvalidContentHash):
        storage.put_if_absent("hash.pdf", b"x", digest)


def test_valid_but_incorrect_hash_is_rejected(s3):
    _, _, storage = s3
    with pytest.raises(InvalidContentHash):
        storage.put_if_absent("hash.pdf", b"x", sha(b"different"))


def test_real_s3_non_condition_errors_propagate(s3):
    client, _, _ = s3
    storage = S3Storage(client, "bucket-that-does-not-exist-" + uuid.uuid4().hex)
    with pytest.raises(ClientError) as error:
        storage.put_if_absent("x.pdf", b"x", sha(b"x"))
    assert error.value.response["Error"]["Code"] == "NoSuchBucket"
