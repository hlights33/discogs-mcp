"""Settings loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    discogs_token: SecretStr | None = Field(default=None, validation_alias="DISCOGS_TOKEN")
    discogs_username: str | None = Field(default=None, validation_alias="DISCOGS_USERNAME")
    discogs_cache_path: Path = Field(
        default=Path("~/.discogs-mcp/cache.db"), validation_alias="DISCOGS_CACHE_PATH"
    )
    discogs_base_url: str = Field(default="https://api.discogs.com", validation_alias="DISCOGS_BASE_URL")
    enable_local_library: bool = Field(default=False, validation_alias="DISCOGS_ENABLE_LOCAL")
    mcp_auth_token: SecretStr | None = Field(default=None, validation_alias="MCP_AUTH_TOKEN")

    @property
    def cache_path(self) -> Path:
        return self.discogs_cache_path.expanduser()

    def token(self) -> str | None:
        return self.discogs_token.get_secret_value() if self.discogs_token else None
