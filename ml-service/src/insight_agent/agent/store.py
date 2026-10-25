"""Сохранение готовых инсайтов на диск, чтобы уточняющие вопросы работали после перезапуска."""
import hashlib
import json
import os
import re
from pathlib import Path

from insight_agent.agent.state import AgentState
from insight_agent.schemas import BankFragment, CheckIssue, EventAnalysis, Insight, InsightRequest, NewsEvent
from insight_agent.tools.profile import derive_signals, missing_profile_data


class InsightStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, insight_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", insight_id)[:80]
        return self.directory / f"{safe}-{hashlib.sha256(insight_id.encode()).hexdigest()[:10]}.json"

    def save(self, state: AgentState, insight: Insight) -> None:
        payload = {
            "corpus_revision": state.corpus_revision,
            "event_content_sha256": state.event_content_sha256,
            "insight": insight.model_dump(mode="json"),
            "request": state.request.model_dump(mode="json"),
            "analysis": state.analysis.model_dump(mode="json") if state.analysis else None,
            "bank": [f.model_dump(mode="json") for f in state.bank.values()],
            "news": [e.model_dump(mode="json") for e in state.news.values()],
            "resolved_news_texts": state.resolved_news_texts,
            "unresolved_sources": sorted(state.unresolved_sources),
            "evidence_issues": [i.model_dump(mode="json") for i in state.evidence_issues],
            "limited_reasons": state.limited_reasons,
            "adaptations": state.adaptations,
            "plan": state.plan,
            "bank_search_broadened": state.bank_search_broadened,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(insight.insight_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def load(self, insight_id: str) -> tuple[AgentState, Insight] | None:
        path = self._path(insight_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
            insight = Insight.model_validate(data["insight"])
            request = InsightRequest.model_validate(data["request"])
            for field in ("corpus_revision", "event_content_sha256"):
                value = getattr(request, field)
                if data.get(field, value) != value or getattr(insight, field) != value:
                    raise ValueError("Saved insight context does not match its request")
            # Older results did not store as_of; modern results also freeze an
            # explicit null. Compare parsed instants, not their offset spelling.
            if "as_of" in data["insight"] and insight.as_of != request.as_of:
                raise ValueError("Saved insight cutoff does not match its request")
        except (OSError, ValueError, KeyError):
            return None
        state = AgentState(
            run_id=insight.run_id,
            request=request,
            signals=derive_signals(request.profile),
            missing_profile=missing_profile_data(request.profile),
            analysis=EventAnalysis.model_validate(data["analysis"]) if data.get("analysis") else None,
            bank={f["fragment_id"]: BankFragment.model_validate(f) for f in data["bank"]},
            news={e["event_id"]: NewsEvent.model_validate(e) for e in data["news"]},
            resolved_news_texts=data["resolved_news_texts"],
            unresolved_sources=set(data["unresolved_sources"]),
            evidence_issues=[CheckIssue.model_validate(i) for i in data["evidence_issues"]],
            limited_reasons=data["limited_reasons"],
            adaptations=data["adaptations"],
            plan=data["plan"],
            bank_search_broadened=data["bank_search_broadened"],
        )
        return state, insight
