from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEBASE_ASSISTANT_", env_file=".env", extra="ignore"
    )

    data_dir: Path = Field(default=Path("../data/backend"))
    sqlite_path: Path = Field(default=Path("../data/backend/app.sqlite3"))
    chroma_dir: Path = Field(default=Path("../data/backend/chroma"))
    clones_dir: Path = Field(default=Path("../data/backend/clones"))
    redis_url: str = "redis://redis:6379/0"
    max_file_bytes: int = 250_000
    max_repo_bytes: int = 200_000_000
    max_indexed_files: int = 20_000
    chunk_max_lines: int = 80
    chunk_overlap_lines: int = 10
    parser_version: str = "plain-symbol-v1"
    openrouter_api_key: str | None = None
    openrouter_chat_model: str = "deepseek/deepseek-chat"
    openrouter_embedding_model: str = "openai/text-embedding-3-small"

    def diagnostics(self) -> dict[str, str | int]:
        return {
            "data_dir": str(self.data_dir),
            "sqlite_path": str(self.sqlite_path),
            "chroma_dir": str(self.chroma_dir),
            "clones_dir": str(self.clones_dir),
            "redis_url": self.redis_url,
            "max_file_bytes": self.max_file_bytes,
            "max_repo_bytes": self.max_repo_bytes,
            "max_indexed_files": self.max_indexed_files,
            "parser_version": self.parser_version,
            "openrouter_chat_model": self.openrouter_chat_model,
            "openrouter_embedding_model": self.openrouter_embedding_model,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
