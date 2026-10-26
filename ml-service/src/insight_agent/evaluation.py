"""Раннер проверки D05: прогоняет пары «событие + профиль» из манифеста и пишет строки отчёта.

Автоматически проверяются только механические условия (исход, признаки, запрещённые фразы, источники)
и считаются M7/M8. Содержательная рубрика (0-2 по пяти измерениям) и M1-M4 заполняются вручную.
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field

from insight_agent.agent.versions import PROMPT_VERSION, git_sha
from insight_agent.schemas import Answer, Insight, InsightRequest, InsightStatus, Outcome
from insight_agent.trace import load_trace

PASS, FAIL, BLOCKED, NOT_RUN = "PASS", "FAIL", "BLOCKED", "NOT_RUN"
RUBRIC = ("relevance_context", "facts_grounding", "depth", "provenance_uncertainty", "next_step")


class Expect(BaseModel):
    outcomes: list[Outcome] | None = None  # допустимые исходы; None - любой, кроме failed
    require_features: list[str] = Field(default_factory=list)
    forbid_features: list[str] = Field(default_factory=list)
    forbid_text: list[str] = Field(default_factory=list)
    min_sources: int = 0


class ManifestRow(BaseModel):
    test_id: str
    fixture_id: str
    case_variant: str = "base"
    pool_id: str | None = None
    dataset_version: str | None = None
    profile_version: str | None = None
    label: int | None = Field(default=None, ge=0, le=2)  # 0 нерелевантно, 1 релевантно, 2 критично
    request: InsightRequest
    expect: Expect = Field(default_factory=Expect)


def load_manifest(path: Path) -> list[ManifestRow]:
    rows = [ManifestRow.model_validate_json(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    ids = [(r.test_id, r.fixture_id, r.case_variant) for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Manifest has duplicate (test_id, fixture_id, case_variant)")
    return rows


def _card_text(insight: Insight | Answer) -> str:
    parts = [getattr(insight, field, "") for field in ("headline", "impact_rationale", "text")]
    for name in ("what_happened", "why_it_matters", "consequences", "risks", "opportunities", "actions", "bank_view", "claims"):
        for claim in getattr(insight, name, []):
            parts.append(claim.text)
            if getattr(claim, "horizon", None):
                parts.append(claim.horizon)
    parts += insight.missing_data + getattr(insight, "model_vs_bank", [])
    for scenario in getattr(insight, "scenarios", []):
        parts += [scenario.name, scenario.trigger, scenario.implication]
    report = getattr(insight, "self_check", None)
    if report:
        parts += [issue.message for issue in report.issues]
    return "\n".join(parts).lower()


def automatic_checks(row: ManifestRow, insight: Insight) -> dict[str, bool]:
    allowed = row.expect.outcomes
    outcome_ok = (insight.outcome in allowed) if allowed is not None else insight.status != InsightStatus.failed
    text = _card_text(insight)
    return {
        "outcome_allowed": outcome_ok,
        "required_features_used": set(row.expect.require_features) <= set(insight.profile_features_used),
        "forbidden_features_unused": not set(row.expect.forbid_features) & set(insight.profile_features_used),
        "forbidden_text_absent": not any(p.lower() in text for p in row.expect.forbid_text),
        "enough_sources": len(insight.sources) >= row.expect.min_sources,
    }


def _commit() -> str:
    if (sha := git_sha()) != "unknown":
        return sha
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, cwd=Path(__file__).parent)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _identity(row: ManifestRow) -> dict:
    return {
        "test_id": row.test_id, "case_variant": row.case_variant, "fixture_id": row.fixture_id, "pool_id": row.pool_id,
        "dataset_version": row.dataset_version, "event_id": row.request.event.event_id,
        "company_id": row.request.profile.company_id, "profile_version": row.profile_version,
        "as_of": row.request.as_of.isoformat() if row.request.as_of else None, "label": row.label,
    }


def _versions(agent, manifest_sha: str) -> dict:
    s = agent.settings
    return {
        "commit_sha": _commit(), "prompt_version": PROMPT_VERSION, "manifest_sha256": manifest_sha,
        "models": {"light": s.llm_model_light, "reasoning": s.llm_model_reasoning, "fallback": s.llm_model_fallback},
        "corpus_revision": agent.corpus_snapshot.revision if agent.corpus_snapshot else None,
        "limits": {"max_iterations": s.agent_max_iterations, "max_tokens_per_run": s.agent_max_tokens_per_run,
                   "max_seconds": s.agent_max_seconds},
    }


def run_manifest(
    agent,
    rows: list[ManifestRow],
    *,
    manifest_sha: str = "",
    dry_run: bool = False,
    blocked_reason: str | None = None,
    max_rub: Decimal | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[dict]:
    report = []
    versions = _versions(agent, manifest_sha)
    for row in rows:
        base = {**_identity(row), **versions, "started_at": clock().isoformat(), "manual": {k: None for k in RUBRIC}}
        reason = "dry run" if dry_run else blocked_reason
        verdict = NOT_RUN if dry_run else BLOCKED
        ledger = agent.daily.ledger
        if reason is None and ledger is not None:
            if max_rub is not None and ledger.spent >= max_rub:
                reason, verdict = f"достигнут предел прогона {max_rub} руб.", BLOCKED
            elif ledger.level() == "stop":
                reason, verdict = "дневной бюджет близок к лимиту", BLOCKED
        if reason is not None:
            report.append({**base, "verdict": verdict, "reason": reason})
            continue
        request = row.request.model_copy(update={"request_id": f"eval:{row.test_id}:{row.fixture_id}:{row.case_variant}"})
        insight = agent.run(request)
        events = load_trace(agent.settings.trace_dir, insight.run_id) or []
        usage = next((e.data.get("usage") for e in reversed(events) if e.type == "final"), None) or {}
        checks = automatic_checks(row, insight)
        report.append({
            **base, "run_id": insight.run_id, "insight_id": insight.insight_id, "version": insight.version,
            "status": insight.status.value, "outcome": insight.outcome.value, "error_code": insight.error_code,
            "cache_hit": insight.cache_hit, "checks": checks, "usage": usage,
            "verdict": PASS if all(checks.values()) else FAIL,
            "reason": None if all(checks.values()) else [k for k, ok in checks.items() if not ok],
        })
    return report


def summarize(report: list[dict]) -> dict:
    ran = [r for r in report if "run_id" in r]
    terminal = [r for r in ran if r["outcome"] != "cancelled"]
    failed = [r for r in terminal if r["status"] == "failed"]
    durations = sorted(int(r["usage"]["duration_ms"]) for r in ran if not r["cache_hit"] and r["usage"].get("duration_ms") is not None)
    total = sum((Decimal(r["usage"].get("cost_rub", "0")) for r in ran if not r["cache_hit"]), Decimal(0))
    successful_new = [r for r in ran if not r["cache_hit"] and r["outcome"] in ("full", "limited")]
    counts = {v: sum(1 for r in report if r["verdict"] == v) for v in (PASS, FAIL, BLOCKED, NOT_RUN)}
    relevant = [r for r in ran if (r.get("label") or 0) >= 1]
    return {
        "rows": len(report), "verdicts": counts,
        "M7_error_rate": {"failed": len(failed), "terminal": len(terminal),
                          "value": f"{len(failed) / len(terminal):.3f}" if terminal else "нет данных"},
        "M7_latency_ms": {"n": len(durations), "p95": durations[math.ceil(0.95 * len(durations)) - 1] if durations else "нет данных",
                          "max": durations[-1] if durations else "нет данных"},
        "M8_cost_rub": {"total": str(total), "new_successful_insights": len(successful_new),
                        "per_insight": str((total / len(successful_new)).quantize(Decimal("0.0001"))) if successful_new else "нет данных"},
        "coverage_for_label_ge_1": {o: sum(1 for r in relevant if r["outcome"] == o) for o in ("full", "limited", "refused", "none")},
        "manual_rubric": "не заполнена: оценка 0-2 по пяти измерениям, M1-M4 считаются вручную",
    }


def write_report(path: Path, report: list[dict], summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"summary": summary}, ensure_ascii=False)] + [json.dumps(r, ensure_ascii=False) for r in report]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, agent_factory: Callable[[], object] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Прогон манифеста D05")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, default=Path("eval-report.jsonl"))
    parser.add_argument("--dry-run", action="store_true", help="ничего не запускать, строки получают NOT_RUN")
    parser.add_argument("--max-rub", type=Decimal, default=None, help="остановить прогон, когда потрачено столько за сутки")
    args = parser.parse_args(argv)

    rows = load_manifest(args.manifest)
    manifest_sha = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    if agent_factory is None:
        from insight_agent.api import build_agent
        from insight_agent.config import get_settings

        settings = get_settings()
        agent_factory = lambda: build_agent(settings)  # noqa: E731
        blocked = None if (settings.llm_api_key or args.dry_run or os.environ.get("EVAL_ALLOW_NO_KEY")) else "не задан LLM_API_KEY"
    else:
        blocked = None
    report = run_manifest(agent_factory(), rows, manifest_sha=manifest_sha, dry_run=args.dry_run,
                          blocked_reason=blocked, max_rub=args.max_rub)
    summary = summarize(report)
    write_report(args.out, report, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["verdicts"][FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
