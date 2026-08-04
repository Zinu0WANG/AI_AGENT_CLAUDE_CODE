from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Settings:
    project_dir: Path
    db_path: Path
    demo_user_id: str
    anthropic_api_key: str
    anthropic_base_url: str | None
    model_id: str
    embedding_api_key: str
    embedding_base_url: str | None
    embedding_model: str

    @property
    def chat_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def embeddings_enabled(self) -> bool:
        return bool(self.embedding_api_key and self.embedding_base_url)


def load_settings(db_path: Path | None = None) -> Settings:
    load_dotenv(PROJECT_DIR / ".env")
    configured_db = Path(os.getenv("MEMORY_DB_PATH", "data/memory_agent.db"))
    if not configured_db.is_absolute():
        configured_db = PROJECT_DIR / configured_db
    return Settings(
        project_dir=PROJECT_DIR,
        db_path=(db_path or configured_db).resolve(),
        demo_user_id=os.getenv("DEMO_USER_ID", "demo-user"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        model_id=os.getenv("MODEL_ID", "claude-sonnet-4-6"),
        embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
        embedding_base_url=os.getenv("EMBEDDING_BASE_URL") or None,
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
    )

