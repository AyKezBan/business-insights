"""SYNTHETIC TEST APP ONLY: real ML pipeline, scripted model, no external calls.

Mount this directory and ml-service/tests on PYTHONPATH. This module deliberately
ignores .env, API keys, model endpoint variables, and the live materials store.
"""
from pathlib import Path
import os

from fakes import (FIXTURES, ScriptedClient, analysis_reply, draft_reply, plan_reply,
                   reviewed_semantics, semantic_contents)
from insight_agent.api import create_app
from insight_agent.agent.orchestrator import InsightAgent
from insight_agent.config import Settings
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.news import NewsStore


def synthetic_draft():
    draft = draft_reply()
    draft["headline"] = "[SYNTHETIC SMOKE] Рост ставки и кредит компании"
    draft["why_it_matters"] = [{"text": "Кредит компании делает стоимость обслуживания долга чувствительной к ставке.",
                               "kind": "inference", "source_ids": ["news_demo_rate"], "profile_features": ["products.loans"]}]
    draft["consequences"] = [{"text": "Изменение стоимости финансирования может повлиять на план закупок компании.",
                             "kind": "inference", "source_ids": ["news_demo_rate"], "profile_features": ["okved_code"]}]
    draft["opportunities"] = []  # U_1 has no observed revenue; do not cite that feature.
    draft["actions"] = [{"text": "Проверить условия ставки по кредиту компании и обновить план платежей.",
                         "kind": "recommendation", "source_ids": ["news_demo_rate"], "profile_features": ["products.loans"]}]
    return draft


def create_smoke_app():
    plan = plan_reply()
    plan["steps"] = [step for step in plan["steps"] if step["tool"] in {"search_bank_analytics", "search_news"}]
    draft = synthetic_draft()
    quoted_fact = analysis_reply()["facts"][0]
    answer = {"answerable": True, "text": quoted_fact["text"], "claims": [quoted_fact]}
    # Whitelist this finite fixture before any requests; arbitrary generated text is never approved.
    reviewed = reviewed_semantics(semantic_contents(draft) + semantic_contents(answer, answer=True))
    scripted = ScriptedClient({
        "analyze_event": [analysis_reply()],
        "plan": [plan],
        "personalize": [draft],
        "revise": [synthetic_draft()],
        "answer": [answer],
        "semantic_verify": [reviewed, reviewed],
    })
    settings = Settings(
        _env_file=None,
        llm_base_url="http://127.0.0.1:9/synthetic-only",
        llm_api_key="",
        llm_model_light="gemma-4-26b-a4b",
        llm_model_reasoning="qwen3.6-35b-a3b",
        llm_model_fallback="glm-5.3-flash",
        llm_daily_token_budget=2_000_000,
        agent_max_iterations=3,
        agent_max_tool_calls=12,
        agent_max_tokens_per_run=40_000,
        bank_fragments_path=FIXTURES / "bank_fragments.jsonl",
        news_path=FIXTURES / "news.jsonl",
        materials_dir=None,
        materials_public_base_url="",
        trace_dir=Path(os.getenv("TRACE_DIR", "/tmp/business-insights-smoke-traces")),
        open_research_enabled=False,
    )
    agent = InsightAgent(scripted, settings, BankIndex.from_jsonl(settings.bank_fragments_path),
                         NewsStore.from_jsonl(settings.news_path))
    app = create_app(agent)

    @app.get("/smoke/health")
    def synthetic_health():
        return {"mode": "synthetic-scripted", "external_llm_calls": 0, "scripted_calls": len(scripted.calls),
                "semantic_reviews": sum(step == "semantic_verify" for step, _ in scripted.calls),
                "warning": "SYNTHETIC TEST DATA ONLY"}

    return app


app = create_smoke_app()
