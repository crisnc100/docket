from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://docket:docket@localhost:55432/docket"  # pragma: allowlist secret
    )
    openrouter_api_key: SecretStr = SecretStr("")
    docket_webhook_secret: SecretStr = SecretStr("")
    docket_webhook_rate_limit: int = Field(default=60, gt=0)
    docket_model: str = "openai/gpt-4o-mini"
    docket_provider: str = "OpenAI"
    docket_daily_budget_usd: float = 1.50

    temporal_address: str = "localhost:7233"
    docket_task_queue: str = "docket"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "docket-local"
    s3_secret_key: SecretStr = SecretStr("docket-local-password")  # pragma: allowlist secret
    s3_bucket: str = "docket"
    docket_model_mode: str = "replay"
    docket_replay_path: str = "evals/recordings/calls.jsonl"
    docket_review_timeout_seconds: float = Field(default=3600, gt=0)
    docket_run_budget_usd: float = Field(default=2, gt=0)
