import uuid

import pytest
from pydantic import SecretStr

from docket.runtime import Runtime
from docket.settings import Settings


class ScriptedModel:
    """Test-only model behavior, never written to provider replay archives."""

    def __init__(self, choice="UNIT-0001", category="lease"):
        self.calls = []
        self.choice = choice
        self.category = category

    async def call(self, *, activity, schema, **kwargs):
        self.calls.append(activity)
        return schema.model_validate(
            {"category": self.category} if activity == "classify" else {"record_id": self.choice}
        )


@pytest.fixture()
def runtime(postgres_engine):
    instance = Runtime(
        Settings(
            s3_bucket="workflow-test-" + uuid.uuid4().hex,
            docket_review_token=SecretStr(uuid.uuid4().hex),
        )
    )
    instance.engine.dispose()
    instance.engine = postgres_engine
    instance.ensure_bucket()
    try:
        yield instance
    finally:
        for item in instance.s3.list_objects_v2(Bucket=instance.settings.s3_bucket).get(
            "Contents", []
        ):
            instance.s3.delete_object(Bucket=instance.settings.s3_bucket, Key=item["Key"])
        instance.s3.delete_bucket(Bucket=instance.settings.s3_bucket)
        instance.close()
