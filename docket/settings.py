from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://docket:docket@localhost:55432/docket"  # pragma: allowlist secret
    )
    openrouter_api_key: SecretStr = SecretStr("")
    docket_model: str = "openai/gpt-4o-mini"
    docket_provider: str = "OpenAI"
    docket_daily_budget_usd: float = 1.50
