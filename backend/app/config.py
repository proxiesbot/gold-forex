from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "app.db"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "xau-ai-research-bot"
    app_version: str = "1.4.0"
    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_default_symbol: str = "XAUUSD"
    app_chart_symbol: str = "XAUUSD"
    app_enable_ollama: bool = True
    app_ollama_base_url: str = "http://localhost:11434"
    app_ollama_model: str = "qwen3:4b"
    app_data_dir: str = str(DEFAULT_DATA_DIR)
    app_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://localhost:8000"])
    app_log_level: str = "INFO"
    app_api_key: str = ""
    app_require_auth_for_write: bool = False
    app_db_path: str = str(DEFAULT_DB_PATH)
    market_data_provider_order: str = "twelvedata,alphavantage,yfinance"
    market_data_http_timeout_seconds: float = 20.0
    market_data_cache_enabled: bool = True
    market_data_cache_ttl_intraday_seconds: int = 300
    market_data_cache_ttl_daily_seconds: int = 1800
    market_data_retry_attempts: int = 2
    market_data_retry_backoff_seconds: float = 0.75
    twelvedata_api_key: str = ""
    alphavantage_api_key: str = ""

    backend_url: str = "http://backend:8000"
    telegram_bot_token: str = ""
    telegram_allowed_users: str = ""

    model_config = SettingsConfigDict(env_file=str(DEFAULT_ENV_FILE), extra="ignore")

    @field_validator("app_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        text = str(value).strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            except json.JSONDecodeError:
                pass
        return [part.strip() for part in text.split(",") if part.strip()]

    @staticmethod
    def _resolve_path(value: str | Path, *, base: Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (base / path).resolve()
        return path

    @property
    def data_dir(self) -> Path:
        return self._resolve_path(self.app_data_dir, base=PROJECT_ROOT)

    @property
    def database_path(self) -> Path:
        raw = Path(self.app_db_path).expanduser()
        if raw.is_absolute():
            return raw
        if raw.parts and raw.parts[0] == "." and len(raw.parts) > 1 and raw.parts[1] == "data":
            return (PROJECT_ROOT / raw).resolve()
        if raw.parts and raw.parts[0] == "data":
            return (PROJECT_ROOT / raw).resolve()
        return (self.data_dir / raw.name).resolve()

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def strategies_dir(self) -> Path:
        return self.data_dir / "strategies"

    def ensure_data_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.strategies_dir.mkdir(parents=True, exist_ok=True)

    def is_telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.is_valid_telegram_token(self.telegram_bot_token))

    def api_auth_enabled(self) -> bool:
        return bool((self.app_api_key or "").strip())

    def should_require_auth(self, method: str) -> bool:
        if not self.api_auth_enabled():
            return False
        if method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return False
        return self.app_require_auth_for_write

    @staticmethod
    def is_valid_telegram_token(token: str | None) -> bool:
        if not token:
            return False
        token = token.strip()
        if ":" not in token:
            return False
        prefix, suffix = token.split(":", 1)
        return prefix.isdigit() and len(suffix) >= 20



def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


settings = Settings()
settings.ensure_data_dirs()
