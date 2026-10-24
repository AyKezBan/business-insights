from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "https://llm.example.invalid/v1"
    llm_api_key: str = ""
    llm_model_light: str = "gemma-4-26b-a4b"
    llm_model_reasoning: str = "qwen3.6-35b-a3b"
    llm_model_fallback: str = "glm-5.3-flash"
    llm_timeout_seconds: float = 60.0
    llm_daily_token_budget: int = 2_000_000

    agent_max_iterations: int = 3
    agent_max_tool_calls: int = 12
    agent_max_tokens_per_run: int = 40_000

    bank_fragments_path: Path = Path("data/bank_fragments.jsonl")
    news_path: Path = Path("data/news.jsonl")
    trace_dir: Path = Path("traces")


@lru_cache
def get_settings() -> Settings:
    return Settings()
