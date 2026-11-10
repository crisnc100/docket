import hashlib
import re
from typing import Protocol, TypedDict, cast
from urllib.parse import quote

from docket.config import Category, Record


class StorageConflict(Exception):
    status_reason = "storage_conflict"
    non_retryable = True


class InvalidStorageKey(ValueError):
    pass


class InvalidContentHash(ValueError):
    pass


def validate_hash(sha256: str) -> None:
    if re.fullmatch(r"[a-f0-9]{64}", sha256) is None:
        raise InvalidContentHash("Expected a lowercase SHA-256 hex digest")


def build_key(record: Record, category: Category | str, sha256: str) -> str:
    validate_hash(sha256)
    if not isinstance(category, Category):
        raise InvalidStorageKey("Category must come from the category enum")
    for component in [record.building, record.record_id, category.value]:
        if re.fullmatch(r"[A-Za-z0-9_-]+", component) is None:
            raise InvalidStorageKey("Unsafe storage key component")
    return f"{record.building}/{record.record_id}/{category.value}/{sha256[:16]}.pdf"


def validate_key(key: str) -> None:
    if not key or any(
        re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?", part) is None
        for part in key.split("/")
    ):
        raise InvalidStorageKey("Unsafe S3 key")


class ReadBody(Protocol):
    def read(self, amt: int = -1) -> bytes: ...
    def close(self) -> None: ...


class GetResponse(TypedDict):
    Body: ReadBody


class S3Exceptions(Protocol):
    @property
    def ClientError(self) -> type[Exception]: ...


class S3Client(Protocol):
    @property
    def exceptions(self) -> S3Exceptions: ...

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        IfNoneMatch: str,
        Metadata: dict[str, str],
        ContentType: str,
    ) -> object: ...
    def get_object(self, *, Bucket: str, Key: str) -> GetResponse: ...


class S3Error(Protocol):
    @property
    def response(self) -> dict[str, dict[str, object]]: ...


class S3Storage:
    def __init__(self, client: S3Client, bucket: str):
        self.client = client
        self.bucket = bucket

    def put_if_absent(
        self, key: str, content: bytes, sha256: str, *, original_filename: str | None = None
    ) -> bool:
        validate_key(key)
        validate_hash(sha256)
        if hashlib.sha256(content).hexdigest() != sha256:
            raise InvalidContentHash("Provided digest does not match the bytes")
        metadata = {"sha256": sha256}
        if original_filename is not None:
            metadata["original-filename"] = quote(original_filename, safe="")
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                IfNoneMatch="*",
                Metadata=metadata,
                ContentType="application/pdf",
            )
            return True
        except self.client.exceptions.ClientError as exc:
            if cast(S3Error, exc).response["Error"]["Code"] != "PreconditionFailed":
                raise
        # Verify the bytes, not ETag (multipart-dependent) or mutable object metadata.
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        digest = hashlib.sha256()
        try:
            while chunk := body.read(1024 * 1024):
                digest.update(chunk)
        finally:
            body.close()
        if digest.hexdigest() != sha256:
            raise StorageConflict("Existing S3 key holds different bytes")
        return False
