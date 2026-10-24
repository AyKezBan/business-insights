"""Deterministic evidence checks; semantic usefulness still needs expert evaluation."""
import re

from insight_agent.agent.state import AgentState
from insight_agent.schemas import CheckIssue, Claim, ClaimKind, EventAnalysis, NewsEvent, PersonalizationDraft, Scenario, SourceKind
from insight_agent.tools.search import tokenize

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_QUANTITY = re.compile(r"(\d+(?:[.,]\d+)?)\s*(%|процент(?:а|ов)?|б\.?\s*п\.?|п\.?\s*п\.?|bps?)", re.I)
NEEDS_SOURCE = {ClaimKind.fact, ClaimKind.assessment}
NEEDS_BASIS = {ClaimKind.inference, ClaimKind.recommendation}
RETELL_OVERLAP = 0.6
SECTIONS = ("why_it_matters", "consequences", "risks", "opportunities", "actions", "bank_view")


def _norm(number: str) -> str:
    number = number.replace(",", ".")
    return number.rstrip("0").rstrip(".") if "." in number else number


def _numbers(text: str) -> set[str]:
    return {_norm(n) for n in _NUMBER.findall(text)}


def _quantities(text: str) -> set[tuple[str, str]]:
    result = set()
    for value, unit in _QUANTITY.findall(text):
        normalized = re.sub(r"[.\s]", "", unit.lower())
        unit = "percent" if normalized == "%" or normalized.startswith("процент") else "bp" if normalized.startswith(("бп", "bp")) else "pp"
        result.add((_norm(value), unit))
    return result


def _issue(code, message, location, severity="error") -> CheckIssue:
    return CheckIssue(code=code, severity=severity, message=message, location=location)


def _references(ids, catalog, texts, location, *, required=False):
    issues = []
    if required and not ids:
        issues.append(_issue("no_source", "Факт или позиция банка без источника", location))
    if unknown := [sid for sid in ids if sid not in catalog]:
        issues.append(_issue("unknown_source", f"Неизвестные источники: {unknown}", location))
    if unresolved := [sid for sid in ids if sid in catalog and not texts.get(sid)]:
        issues.append(_issue("unresolved_source", f"Нет текста версии источника: {unresolved}", location))
    return issues


def _numeric_issues(text, cited, location, *, strict, profile=""):
    foreign = sorted(_numbers(text) - (_numbers(cited) | _numbers(profile)))
    if foreign:
        return [_issue("unsupported_number", f"Числа не найдены в основаниях: {foreign}", location,
                       "error" if strict else "warning")]
    if strict and (unknown := _quantities(text) - _quantities(cited)):
        return [_issue("unsupported_quantity", f"Значение/единица не найдены в источнике: {sorted(unknown)}", location)]
    return []


def check_claims(claims: list[Claim], state: AgentState, *, section: str) -> list[CheckIssue]:
    catalog, texts, features = state.sources(), state.source_texts(), state.signal_features
    issues = []
    for i, claim in enumerate(claims):
        loc = f"{section}[{i}]"
        issues += _references(claim.source_ids, catalog, texts, loc, required=claim.kind in NEEDS_SOURCE)
        if bad := [f for f in claim.profile_features if f not in features]:
            issues.append(_issue("unknown_feature", f"Признаков нет в профиле: {bad}", loc))
        if claim.kind in NEEDS_BASIS and not claim.source_ids and not claim.profile_features:
            issues.append(_issue("no_basis", "Вывод без источников и признаков компании", loc))
        if claim.kind == ClaimKind.assessment:
            if not claim.source_ids or any(sid not in catalog or catalog[sid].kind != SourceKind.bank for sid in claim.source_ids):
                issues.append(_issue("bank_view_without_bank_source", "Позиция банка должна ссылаться только на материалы банка", loc))
            elif any(sid not in state.bank or state.bank[sid].review_status != "verified"
                     or state.bank[sid].statement_type not in ("bank_assessment", "forecast") for sid in claim.source_ids):
                issues.append(_issue("unverified_bank_assessment", "Фрагмент не подтверждает проверенную позицию банка", loc))
        if section == "bank_view" and claim.kind not in (ClaimKind.assessment, ClaimKind.unavailable):
            issues.append(_issue("bank_view_kind", "В разделе банка допустимы только assessment и unavailable", loc))
        cited = " ".join(texts.get(sid, "") for sid in claim.source_ids)
        issues += _numeric_issues(claim.text, cited, loc, strict=claim.kind in NEEDS_SOURCE,
                                 profile="" if claim.kind in NEEDS_SOURCE else state.profile_text())
    return issues


def check_event_analysis(event: NewsEvent, analysis: EventAnalysis, source_texts: dict[str, str]) -> list[CheckIssue]:
    refs = {ref.source_id: ref for ref in [event.source] + event.related_sources}
    issues = []
    for i, claim in enumerate(analysis.facts):
        loc = f"facts[{i}]"
        issues += _references(claim.source_ids, refs, source_texts, loc, required=True)
        if claim.kind != ClaimKind.fact:
            issues.append(_issue("event_fact_kind", "Общий разбор должен содержать факты", loc))
        cited = " ".join(source_texts.get(sid, "") for sid in claim.source_ids)
        issues += _numeric_issues(claim.text, cited, loc, strict=True)
    return issues


def check_scenarios(scenarios: list[Scenario], state: AgentState) -> list[CheckIssue]:
    catalog, texts = state.sources(), state.source_texts()
    issues = []
    for i, scenario in enumerate(scenarios):
        loc = f"scenarios[{i}]"
        issues += _references(scenario.source_ids, catalog, texts, loc)
        if not scenario.source_ids:
            issues.append(_issue("unanchored_scenario", "Сценарий — гипотеза без отдельного источника", loc, "warning"))
        text = " ".join((scenario.name, scenario.trigger, scenario.implication))
        cited = " ".join(texts.get(sid, "") for sid in scenario.source_ids)
        issues += _numeric_issues(text, cited, loc, strict=bool(scenario.source_ids), profile=state.profile_text())
    return issues


def _claims(draft):
    return [(f"{name}[{i}]", claim) for name in SECTIONS for i, claim in enumerate(getattr(draft, name))]


def _overlap(a, b):
    ta, tb = set(tokenize(a)), set(tokenize(b))
    return len(ta & tb) / len(ta) if ta else 0.0


def check(state: AgentState, draft: PersonalizationDraft) -> list[CheckIssue]:
    issues = []
    for name in ("why_it_matters", "consequences", "actions", "bank_view"):
        if not getattr(draft, name):
            issues.append(_issue("missing_section", f"Раздел {name} пуст", name))
    if not draft.risks and not draft.opportunities:
        issues.append(_issue("missing_section", "Нет ни рисков, ни возможностей", "risks"))
    for section in SECTIONS:
        issues += check_claims(getattr(draft, section), state, section=section)
    issues += check_scenarios(draft.scenarios, state)
    if state.bank and all(c.kind == ClaimKind.unavailable for c in draft.bank_view):
        issues.append(_issue("bank_material_unused", "Материалы банка найдены, но не использованы", "bank_view", "warning"))
    used = {f for _, c in _claims(draft) for f in c.profile_features}
    if not any(c.profile_features for c in draft.why_it_matters):
        issues.append(_issue("not_personalized", "Почему это важно не опирается на признаки компании", "why_it_matters"))
    if len(state.signals) >= 2 and len(used) < 2:
        issues.append(_issue("single_factor", "Вывод учитывает меньше двух признаков компании", None))
    for i, claim in enumerate(draft.consequences):
        if _overlap(claim.text, state.event.text) > RETELL_OVERLAP:
            issues.append(_issue("retelling", "Последствие похоже на пересказ новости", f"consequences[{i}]", "warning"))
    for i, claim in enumerate(draft.actions):
        if not claim.profile_features:
            issues.append(_issue("generic_action", "Действие не привязано к признакам компании", f"actions[{i}]", "warning"))
    return issues


def has_errors(issues: list[CheckIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)
