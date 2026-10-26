from functools import lru_cache
from decimal import Decimal
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
    # Бюджет в рублях на общий ключ. Тарифы провайдера уточнить: цены по умолчанию консервативные.
    llm_daily_budget_rub: Decimal = Decimal(500)
    llm_budget_warn_rub: Decimal = Decimal(350)
    llm_budget_stop_optional_rub: Decimal = Decimal(450)
    llm_price_default_in_rub_per_1k: Decimal = Decimal("0.5")
    llm_price_default_out_rub_per_1k: Decimal = Decimal("1.5")
    llm_prices_json: str = ""
    budget_timezone: str = "Europe/Moscow"
    budget_state_path: Path | None = None

    agent_max_iterations: int = 3
    agent_max_tool_calls: int = 12
    agent_max_tokens_per_run: int = 40_000
    agent_max_seconds: float = 120.0

    bank_fragments_path: Path = Path("data/bank_fragments.jsonl")
    news_path: Path = Path("data/news.jsonl")
    materials_dir: Path | None = None
    materials_public_base_url: str = ""
    materials_cache_size: int = 4
    bank_max_age_days: int = 180
    # Открытые официальные источники (cbr.ru): ставка, курсы, пресс-релизы. Без LLM, только по списку хостов.
    open_research_enabled: bool = True
    open_research_cache_dir: Path | None = None
    trace_dir: Path = Path("traces")


@lru_cache
def get_settings() -> Settings:
    return Settings()
