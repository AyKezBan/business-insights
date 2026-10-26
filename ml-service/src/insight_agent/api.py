from fastapi import FastAPI, HTTPException, Query

from insight_agent import __version__
from insight_agent.agent.orchestrator import InsightAgent
from insight_agent.agent.qa import answer_question
from insight_agent.config import Settings, get_settings
from insight_agent.llm import ChatClient, OpenAICompatibleClient
from insight_agent.schemas import Answer, Insight, InsightRequest, NewsPage, QuestionRequest, TraceEvent
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.news import NewsStore
from insight_agent.trace import load_trace
from insight_agent.materials.api import create_material_router
from insight_agent.materials.store import load_snapshot


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
            "corpus_revision": agent.corpus_snapshot.revision if agent.corpus_snapshot else None,
            "verified_bank_fragments": sum(f.review_status == "verified" for f in agent.bank.fragments),
        }

    @app.post("/v1/insights", response_model=Insight)
    def create_insight(request: InsightRequest) -> Insight:
        return agent.run(request)

    @app.get("/v1/materials/events", response_model=NewsPage)
    def news_events(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1_000_000)) -> NewsPage:
        # This reads the same immutable corpus snapshot used by analysis; no collection or model calls.
        ordered = sorted(agent.news.events,
            key=lambda event: (event.published_at.timestamp() if event.published_at else float("-inf"), event.event_id),
            reverse=True)
        items = ordered[offset:offset + limit]
        next_offset = offset + len(items)
        return NewsPage(corpus_revision=agent.corpus_snapshot.revision if agent.corpus_snapshot else None,
            items=items, next_offset=next_offset if next_offset < len(ordered) else None)

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
