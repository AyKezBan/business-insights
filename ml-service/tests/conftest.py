import pytest

from fakes import FIXTURES
from insight_agent.agent.orchestrator import InsightAgent
from insight_agent.config import Settings
from insight_agent.schemas import InsightRequest
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.news import NewsStore


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        llm_api_key="test-key",
        open_research_enabled=False,
        llm_model_light="gemma-4-26b-a4b",
        llm_model_reasoning="qwen3.6-35b-a3b",
        llm_model_fallback="glm-5.3-flash",
        bank_fragments_path=FIXTURES / "bank_fragments.jsonl",
        news_path=FIXTURES / "news.jsonl",
        trace_dir=tmp_path / "traces",
    )


@pytest.fixture
def request_() -> InsightRequest:
    return InsightRequest.model_validate_json((FIXTURES / "request.json").read_text("utf-8"))


@pytest.fixture
def make_agent(settings):
    def factory(client, *, bank: BankIndex | None = None) -> InsightAgent:
        return InsightAgent(
            client,
            settings,
            bank if bank is not None else BankIndex.from_jsonl(settings.bank_fragments_path),
            NewsStore.from_jsonl(settings.news_path),
        )

    return factory
