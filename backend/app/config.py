"""Runtime configuration, read from the environment (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    falkordb_host: str = os.getenv("FALKORDB_HOST", "localhost")
    falkordb_port: int = int(os.getenv("FALKORDB_PORT", "6381"))
    falkordb_username: str | None = os.getenv("FALKORDB_USERNAME") or None
    falkordb_password: str | None = os.getenv("FALKORDB_PASSWORD") or None
    falkordb_tls: bool = os.getenv("FALKORDB_TLS", os.getenv("FALKORDB_SSL", "false")).lower() in ("1", "true", "yes")
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY") or None
    llm_model: str = os.getenv("LLM_MODEL", "claude-opus-5-5")
    llm_effort: str = os.getenv("LLM_EFFORT", "low")
    # Set LLM_DISABLED=1 to force scripted mode even when a key is present.
    llm_disabled: bool = os.getenv("LLM_DISABLED", "false").lower() in ("1", "true", "yes")
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")
    graph_prefix: str = os.getenv("GRAPH_PREFIX", "town")
    # Free-tier guard: keep at most this many player worlds; the least recently active are deleted.
    max_worlds: int = int(os.getenv("MAX_WORLDS", "20"))
    demo_player: str = os.getenv("DEMO_PLAYER", "demo")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key) and not self.llm_disabled

    @property
    def mode(self) -> str:
        return "llm" if self.llm_enabled else "scripted"


settings = Settings()
