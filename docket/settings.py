from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPLAY_PATHS = {
    "anthropic": "evals/recordings/anthropic.jsonl",
    "openrouter": "evals/recordings/calls.jsonl",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://docket:docket@localhost:55432/docket"  # pragma: allowlist secret
    )
    openrouter_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    docket_review_token: SecretStr | None = None
    docket_webhook_secret: SecretStr = SecretStr("")
    docket_webhook_rate_limit: int = Field(default=60, gt=0)
    docket_model_provider: Literal["anthropic", "openrouter"] = "anthropic"
    docket_model: str = "claude-haiku-5-5"
    docket_provider: str = "OpenAI"
    docket_daily_budget_usd: float = 1.50

    temporal_address: str = "localhost:7233"
    docket_task_queue: str = "docket"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "docket-local"
    s3_secret_key: SecretStr = SecretStr("docket-local-password")  # pragma: allowlist secret
    s3_bucket: str = "docket"
    docket_model_mode: str = "replay"
    # Empty means the provider's own archive, so live and simulated answers never share a file.
    docket_replay_path: str = ""
    docket_review_timeout_seconds: float = Field(default=3600, gt=0)
    docket_run_budget_usd: float = Field(default=2, gt=0)
    docket_run_budget_cap_usd: float = Field(default=5, gt=0)

    @model_validator(mode="after")
    def provider_archive(self) -> "Settings":
        if not self.docket_replay_path:
            self.docket_replay_path = REPLAY_PATHS[self.docket_model_provider]
        return self

    def api_key(self) -> SecretStr:
        if self.docket_model_provider == "anthropic":
            return self.anthropic_api_key
        return self.openrouter_api_key
