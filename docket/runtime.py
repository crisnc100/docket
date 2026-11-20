from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import create_engine

from docket.beta_samples import live_answers_on
from docket.core.storage import S3Client, S3Storage
from docket.llm.anthropic import AnthropicTransport
from docket.llm.budget import Budget, DailyBudget, RunBudget, load_prices
from docket.llm.gateway import MAX_OUTPUT_TOKENS, Gateway, Mode, OpenRouterTransport
from docket.llm.replay import ReplayStore
from docket.settings import Settings

# Live documents each get a gateway, which holds the document's run cap in memory, so the
# worker keeps only the most recent ones (about 23 MiB at this size). An evicted document
# that calls again, like any document after a restart, starts a fresh run cap. The daily
# ledger in Postgres is the hard bound on spend.
MAX_GATEWAYS = 10_000


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
        self.gateways: OrderedDict[tuple[str, str, str, str], Gateway] = OrderedDict()
        # One transport per provider and model, so evicting a gateway closes no HTTP client.
        self.transports: dict[tuple[str, str], Any] = {}

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
        # Live documents have separate budgets; observe runs share their evaluation budget.
        document = request["document_id"] if request["run_id"] == "live" else ""
        key = (request["run_id"], document, request["model_mode"], request["model"])
        if key in self.gateways:
            self.gateways.move_to_end(key)
        else:
            mode = Mode(request["model_mode"])
            settings = self.settings.model_copy(update={"docket_model": request["model"]})
            kwargs = {}
            if mode in {Mode.RECORD, Mode.LIVE}:
                prices = load_prices().ceiling(request["model"])
                provider = settings.docket_model_provider
                if (provider, request["model"]) not in self.transports:
                    self.transports[provider, request["model"]] = (
                        AnthropicTransport(settings, MAX_OUTPUT_TOKENS)
                        if provider == "anthropic"
                        else OpenRouterTransport(settings, prices, MAX_OUTPUT_TOKENS)
                    )
                transport = self.transports[provider, request["model"]]
                if settings.docket_env == "beta":
                    kwargs["live_allowed"] = lambda: live_answers_on(self.engine)
                kwargs |= {
                    "budget": Budget(
                        RunBudget(Decimal(request["budget"])),
                        DailyBudget(self.engine, Decimal(str(settings.docket_daily_budget_usd))),
                    ),
                    "transport": transport,
                    "prices": prices,
                }
            self.gateways[key] = Gateway(
                mode=mode,
                model=request["model"],
                store=ReplayStore(Path(settings.docket_replay_path)),
                provider=settings.docket_model_provider,
                **kwargs,
            )
            if len(self.gateways) > MAX_GATEWAYS:
                self.gateways.popitem(last=False)
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
