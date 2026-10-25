from fastapi import FastAPI, HTTPException

from insight_agent import __version__
from insight_agent.agent.orchestrator import InsightAgent
from insight_agent.agent.qa import answer_question
from insight_agent.config import Settings, get_settings
from insight_agent.llm import ChatClient, OpenAICompatibleClient
from insight_agent.schemas import Answer, Insight, InsightRequest, QuestionRequest, TraceEvent
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.news import NewsStore
from insight_agent.trace import load_trace


def build_agent(settings: Settings, client: ChatClient | None = None) -> InsightAgent:
    client = client or OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key, settings.llm_timeout_seconds)
    return InsightAgent(
        client,
        settings,
        BankIndex.from_jsonl(settings.bank_fragments_path),
        NewsStore.from_jsonl(settings.news_path),
    )


def create_app(agent: InsightAgent | None = None) -> FastAPI:
    settings = agent.settings if agent else get_settings()
    agent = agent or build_agent(settings)
    app = FastAPI(title="Insight Agent", version=__version__)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "bank_fragments": len(agent.bank.fragments),
            "news": len(agent.news.events),
            "daily_tokens_used": agent.daily.used,
        }

    @app.post("/v1/insights", response_model=Insight)
    def create_insight(request: InsightRequest) -> Insight:
        return agent.run(request)

    @app.post("/v1/insights/{insight_id}/questions", response_model=Answer)
    def ask(insight_id: str, body: QuestionRequest) -> Answer:
        answer = answer_question(agent, insight_id, body.question)
        if answer is None:
            raise HTTPException(404, "Инсайт не найден или устарел")
        return answer

    @app.get("/v1/runs/{run_id}/trace", response_model=list[TraceEvent])
    def trace(run_id: str) -> list[TraceEvent]:
        events = load_trace(settings.trace_dir, run_id)
        if events is None:
            raise HTTPException(404, "Трасса не найдена")
        return events

    return app
