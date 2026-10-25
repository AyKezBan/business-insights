from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from insight_agent import __version__
from insight_agent.agent.orchestrator import InsightAgent, RequestIdConflict
from insight_agent.agent.qa import answer_question
from insight_agent.config import Settings, get_settings
from insight_agent.frontend_adapter import VIEWS, to_frontend_card, to_frontend_clarification
from insight_agent.llm import ChatClient, OpenAICompatibleClient
from insight_agent.schemas import Answer, Insight, InsightRequest, QuestionRequest, TraceEvent
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.news import NewsStore
from insight_agent.trace import load_trace
from insight_agent.materials.api import create_material_router
from insight_agent.materials.store import load_snapshot


class ClarificationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    request_id: str | None = Field(default=None, max_length=128)
    view: str = "overview"


def build_agent(settings: Settings, client: ChatClient | None = None) -> InsightAgent:
    client = client or OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key, settings.llm_timeout_seconds)
    snapshot = load_snapshot(settings.materials_dir) if settings.materials_dir is not None else None
    fragments = [] if snapshot is None else [f.model_copy(update={"source_url":
        f"{settings.materials_public_base_url.rstrip('/')}/v1/materials/documents/{f.doc_id}/file#page={f.page}"})
        for f in snapshot.bank_fragments]
    return InsightAgent(
        client,
        settings,
        BankIndex(fragments) if snapshot is not None else BankIndex.from_jsonl(settings.bank_fragments_path),
        NewsStore(snapshot.news, snapshot.source_versions) if snapshot is not None else NewsStore.from_jsonl(settings.news_path),
        corpus_snapshot=snapshot,
    )


def create_app(agent: InsightAgent | None = None) -> FastAPI:
    settings = agent.settings if agent else get_settings()
    agent = agent or build_agent(settings)
    app = FastAPI(title="Insight Agent", version=__version__)
    if agent.corpus_snapshot is not None and settings.materials_dir is not None:
        app.include_router(create_material_router(settings.materials_dir, agent.corpus_snapshot))

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "bank_fragments": len(agent.bank.fragments),
            "news": len(agent.news.events),
            "daily_tokens_used": agent.daily.used,
            "daily_spent_rub": str(agent.daily.ledger.spent) if agent.daily.ledger else None,
            "daily_limit_rub": str(agent.daily.ledger.limit) if agent.daily.ledger else None,
            "corpus_revision": agent.corpus_snapshot.revision if agent.corpus_snapshot else None,
            "verified_bank_fragments": sum(f.review_status == "verified" for f in agent.bank.fragments),
        }

    @app.post("/v1/insights", response_model=Insight)
    def create_insight(request: InsightRequest) -> Insight:
        try:
            return agent.run(request)
        except RequestIdConflict:
            raise HTTPException(409, "request_id уже использован с другим содержимым запроса")

    @app.post("/v1/insights/{insight_id}/questions", response_model=Answer)
    def ask(insight_id: str, body: QuestionRequest) -> Answer:
        answer = answer_question(agent, insight_id, body.question)
        if answer is None:
            raise HTTPException(404, "Инсайт не найден или устарел")
        return answer

    @app.get("/v1/insights/{insight_id}/card")
    def card(insight_id: str, view: str = Query(default="overview")) -> dict:
        if view not in VIEWS:
            raise HTTPException(422, f"view должен быть одним из: {', '.join(VIEWS)}")
        record = agent.get_record(insight_id)
        if record is None:
            raise HTTPException(404, "Инсайт не найден или устарел")
        return to_frontend_card(record.insight, view)

    @app.post("/v1/insights/{insight_id}/clarification")
    def clarification(insight_id: str, body: ClarificationRequest) -> dict:
        if body.view not in VIEWS:
            raise HTTPException(422, f"view должен быть одним из: {', '.join(VIEWS)}")
        answer = answer_question(agent, insight_id, body.question)
        if answer is None:
            raise HTTPException(404, "Инсайт не найден или устарел")
        return to_frontend_clarification(answer, agent.get_record(insight_id).insight, request_id=body.request_id, view=body.view)

    @app.get("/v1/runs/{run_id}/trace", response_model=list[TraceEvent])
    def trace(run_id: str) -> list[TraceEvent]:
        events = load_trace(settings.trace_dir, run_id)
        if events is None:
            raise HTTPException(404, "Трасса не найдена")
        return events

    return app
