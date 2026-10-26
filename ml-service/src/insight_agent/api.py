from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from insight_agent import __version__
from insight_agent.agent.orchestrator import InsightAgent, RequestIdConflict
from insight_agent.agent.qa import answer_question
from insight_agent.config import Settings, get_settings
from insight_agent.frontend_adapter import VIEWS, to_frontend_card, to_frontend_clarification
from insight_agent.llm import ChatClient, OpenAICompatibleClient
from insight_agent.schemas import Answer, Insight, InsightRequest, NewsPage, QuestionRequest, Sha256, TraceEvent
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.news import NewsStore
from insight_agent.trace import load_trace
from insight_agent.materials.api import create_material_router
from insight_agent.materials.runtime import CorpusError, CorpusRegistry


class ClarificationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    request_id: str | None = Field(default=None, max_length=128)
    view: str = "overview"


def build_agent(settings: Settings, client: ChatClient | None = None) -> InsightAgent:
    client = client or OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key, settings.llm_timeout_seconds,
                                                  settings.llm_max_output_tokens)
    registry = CorpusRegistry(settings.materials_dir, settings.materials_public_base_url,
                              settings.materials_cache_size) if settings.materials_dir is not None else None
    bundle = registry.current() if registry else None
    return InsightAgent(
        client,
        settings,
        bundle.bank if bundle else BankIndex.from_jsonl(settings.bank_fragments_path),
        bundle.news if bundle else NewsStore.from_jsonl(settings.news_path),
        corpus_snapshot=bundle.snapshot if bundle else None,
        corpus_registry=registry,
    )


def create_app(agent: InsightAgent | None = None) -> FastAPI:
    settings = agent.settings if agent else get_settings()
    agent = agent or build_agent(settings)
    app = FastAPI(title="Insight Agent", version=__version__)
    if agent.corpus_registry is not None:
        app.include_router(create_material_router(agent.corpus_registry))

    @app.get("/health")
    def health() -> dict:
        result = {
            "status": "ok",
            "bank_fragments": len(agent.bank.fragments),
            "news": len(agent.news.events),
            "daily_tokens_used": agent.daily.used,
            "open_research": agent.research is not None,
            "daily_spent_rub": str(agent.daily.ledger.spent) if agent.daily.ledger else None,
            "daily_limit_rub": str(agent.daily.ledger.limit) if agent.daily.ledger else None,
            "corpus_revision": agent.corpus_snapshot.revision if agent.corpus_snapshot else None,
            "verified_bank_fragments": sum(f.review_status == "verified" for f in agent.bank.fragments),
        }
        if agent.corpus_registry:
            result.update(agent.corpus_registry.health())
            if result["last_refresh_error"] or result["source_health"] in {"degraded", "failed"}:
                result["status"] = "degraded"
        return result

    @app.post("/v1/insights", response_model=Insight)
    def create_insight(request: InsightRequest) -> Insight:
        try:
            return agent.run(request)
        except RequestIdConflict:
            raise HTTPException(409, "request_id уже использован с другим содержимым запроса")
        except CorpusError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from None

    @app.get("/v1/materials/news", response_model=NewsPage)
    def news_events(limit: int = Query(default=20, ge=1, le=100),
                    offset: int = Query(default=0, ge=0, le=1_000_000),
                    revision: Sha256 | None = None) -> NewsPage:
        # Offset pagination is separate from the token/cursor catalog at /materials/events.
        # Pin the returned revision on later pages when the current corpus can change.
        if agent.corpus_registry:
            try:
                bundle = agent.corpus_registry.get(revision) if revision else agent.corpus_registry.current()
            except (CorpusError, KeyError, OSError, ValueError, TypeError):
                raise HTTPException(503, {"code": "corpus_unavailable"}) from None
            events = bundle.news.events
            corpus_revision = bundle.revision
        else:
            corpus_revision = agent.corpus_snapshot.revision if agent.corpus_snapshot else None
            if revision is not None and revision != corpus_revision:
                raise HTTPException(409, {"code": "corpus_unavailable"})
            events = agent.news.events
        ordered = sorted(events,
            key=lambda event: (event.published_at.timestamp() if event.published_at else float("-inf"), event.event_id),
            reverse=True)
        items = ordered[offset:offset + limit]
        next_offset = offset + len(items)
        return NewsPage(corpus_revision=corpus_revision, items=items,
                        next_offset=next_offset if next_offset < len(ordered) else None)

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
