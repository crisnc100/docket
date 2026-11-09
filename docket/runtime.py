from decimal import Decimal
from pathlib import Path
from typing import cast

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import create_engine

from docket.core.storage import S3Client, S3Storage
from docket.llm.budget import Budget, DailyBudget, PriceCeiling, RunBudget
from docket.llm.gateway import Gateway, Mode, OpenRouterTransport
from docket.llm.replay import ReplayStore
from docket.settings import Settings


class Runtime:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.engine = create_engine(self.settings.database_url, pool_pre_ping=True)
        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key.get_secret_value(),
            region_name="us-east-1",
            config=Config(
                connect_timeout=2,
                read_timeout=5,
                retries={"max_attempts": 0},
                s3={"addressing_style": "path"},
            ),
        )
        self.storage = S3Storage(cast(S3Client, self.s3), self.settings.s3_bucket)
        self.gateways: dict[tuple[str, str, str], Gateway] = {}

    def ensure_bucket(self) -> None:
        try:
            self.s3.head_bucket(Bucket=self.settings.s3_bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket"}:
                raise
            try:
                self.s3.create_bucket(Bucket=self.settings.s3_bucket)
            except ClientError as created:
                if created.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                    raise

    def gateway(self, request: dict) -> Gateway:
        key = (request["run_id"], request["model_mode"], request["model"])
        if key not in self.gateways:
            mode = Mode(request["model_mode"])
            settings = self.settings.model_copy(update={"docket_model": request["model"]})
            kwargs = {}
            if mode in {Mode.RECORD, Mode.LIVE}:
                kwargs = {
                    "budget": Budget(
                        RunBudget(Decimal(request["budget"])),
                        DailyBudget(self.engine, Decimal(str(settings.docket_daily_budget_usd))),
                    ),
                    "transport": OpenRouterTransport(settings, PriceCeiling(), 256),
                }
            self.gateways[key] = Gateway(
                mode=mode,
                model=request["model"],
                store=ReplayStore(Path(settings.docket_replay_path)),
                **kwargs,
            )
        return self.gateways[key]

    def read(self, key: str) -> bytes:
        body = self.s3.get_object(Bucket=self.settings.s3_bucket, Key=key)["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def close(self) -> None:
        self.engine.dispose()
        self.s3.close()
