import os
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Search for .env in current working dir, parent dirs, or package dir
load_dotenv(dotenv_path=Path.cwd() / ".env")


class AppConfig(BaseModel):
    venice_api_key: str = Field(default_factory=lambda: os.getenv("VENICE_API_KEY", ""))
    venice_base_url: str = Field(default_factory=lambda: os.getenv("VENICE_BASE_URL", "https://api.venice.ai/api/v1").rstrip("/"))
    web_host: str = Field(default_factory=lambda: os.getenv("WEB_HOST", "0.0.0.0"))
    web_port: int = Field(default_factory=lambda: int(os.getenv("WEB_PORT", "8660")))
    web_secret_key: str = Field(default_factory=lambda: os.getenv("WEB_SECRET_KEY", "venice-km-insecure-secret-key-change-me"))
    low_usd_warning_threshold: float = Field(default_factory=lambda: float(os.getenv("LOW_USD_WARNING_THRESHOLD", "0.20")))
    telegram_bot_token: Optional[str] = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN") or None)
    telegram_allowed_users: List[int] = Field(default_factory=lambda: [
        int(uid.strip())
        for uid in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
        if uid.strip().isdigit()
    ])
    mcp_port: int = Field(default_factory=lambda: int(os.getenv("MCP_PORT", "8661")))
    data_dir: Path = Field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")))

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


config = AppConfig()
