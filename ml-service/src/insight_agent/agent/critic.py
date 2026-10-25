"""Deterministic evidence checks; semantic usefulness still needs expert evaluation."""
import re
from decimal import Decimal

from insight_agent.agent.state import AgentState
from insight_agent.schemas import CheckIssue, Claim, ClaimKind, EventAnalysis, NewsEvent, PersonalizationDraft, Scenario, SourceKind
from insight_agent.tools.search import tokenize

_QUANTITY = re.compile(r"(\d+(?:[.,]\d+)?)\s*(%|процент(?:а|ов)?|б\.?\s*п\.?|п\.?\s*п\.?|bps?)", re.I)
NEEDS_SOURCE = {ClaimKind.fact, ClaimKind.assessment}
NEEDS_BASIS = {ClaimKind.inference, ClaimKind.recommendation}
RETELL_OVERLAP = 0.6
SECTIONS = ("why_it_matters", "consequences", "risks", "opportunities", "actions", "bank_view")


_NUMBER = re.compile(
    r"(?<!\d)(\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)|\d+)(?:[.,](\d+))?(?:[ \u00a0\u202f]*(трлн|млрд|млн|тыс)(?![а-яё]))?",
    re.IGNORECASE,
)
_SCALE = {"тыс": Decimal(10) ** 3, "млн": Decimal(10) ** 6, "млрд": Decimal(10) ** 9, "трлн": Decimal(10) ** 12}


def _norm(number: str) -> str:
    number = number.replace(",", ".")
    return number.rstrip("0").rstrip(".") if "." in number else number


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _numbers(text: str) -> set[str]:
    """Числа из текста в каноничном виде; число с «млн/млрд» даёт и сокращённую, и полную запись."""
    found: set[str] = set()
    for whole, fraction, unit in _NUMBER.findall(text):
        value = Decimal(re.sub(r"\D", "", whole) + (f".{fraction}" if fraction else ""))
        found.add(_plain(value))
        if unit:
            found.add(_plain(value * _SCALE[unit.lower()]))
    return found


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


_MODEL_KINDS = (ClaimKind.inference, ClaimKind.recommendation)
_INVESTMENT_ADVICE = re.compile(r"\b(купите|покупайте|продайте|продавайте|инвестируйте|вложите(?:сь)?)\b", re.IGNORECASE)
_CONTRACT_EFFECT = re.compile(
    r"(платеж\w*|выплат\w*|процентн\w+\s+(?:расход|нагрузк)\w*|переплат\w*)[^.;]{0,40}"
    r"\b(вырастет|вырастут|увеличится|увеличатся|возрастет|возрастут|снизится|снизятся|сократится|составит|составят)\b",
    re.IGNORECASE,
)
_CATEGORICAL = re.compile(r"\b(обязательно|гарантированно|неизбежно|без сомнени\w+|стопроцентно)\b", re.IGNORECASE)


def _language_issues(claim: Claim, location: str) -> list[CheckIssue]:
    """Условия договоров компании неизвестны, личных инвестиционных советов сервис не даёт."""
    issues = []
    text = claim.text.replace("ё", "е").replace("Ё", "Е")
    if claim.kind in (*_MODEL_KINDS, ClaimKind.hypothesis) and _INVESTMENT_ADVICE.search(text):
        issues.append(_issue("investment_advice", "Персональные инвестиционные рекомендации недопустимы", location))
    if claim.kind in _MODEL_KINDS and _CONTRACT_EFFECT.search(text):
        issues.append(_issue("unknown_terms_as_fact",
                             "Ставка, срок и график платежей по договорам неизвестны: изменение платежа можно подать только как гипотезу",
                             location))
    if claim.kind in _MODEL_KINDS and _CATEGORICAL.search(text):
        issues.append(_issue("categorical_language", "Категоричная формулировка без оснований", location, "warning"))
    return issues


_KEYRATE_SOURCE = "cbr_keyrate_"
_KEYRATE_CURRENT = re.compile(r"на \d{2}\.\d{2}\.\d{4}: (\d+[.,]\d+)% годовых")
_KEYRATE_PREVIOUS = re.compile(r"Предыдущее значение: (\d+[.,]\d+)% годовых")
_PAST_MARKER = re.compile(r"предыдущ|ранее|раньше|прежн|было|составляла|снизил|повысил|снижен|повышен|до этого", re.IGNORECASE)
_ABOUT_KEYRATE = re.compile(r"ключев|ставк", re.IGNORECASE)


def _keyrate_issues(claim: Claim, texts: dict[str, str], location: str) -> list[CheckIssue]:
    """Число из прошлого значения ставки нельзя выдавать за текущее: оба числа есть в тексте источника."""
    if not _ABOUT_KEYRATE.search(claim.text):
        return []
    percents = {value for value, unit in _quantities(claim.text) if unit == "percent"}
    for sid in claim.source_ids:
        text = texts.get(sid, "")
        if not sid.startswith(_KEYRATE_SOURCE) or not (current := _KEYRATE_CURRENT.search(text)):
            continue
        previous = _KEYRATE_PREVIOUS.search(text)
        old, now = (_norm(previous.group(1)) if previous else None), _norm(current.group(1))
        if old and old != now and old in percents and now not in percents and not _PAST_MARKER.search(claim.text):
            return [_issue("keyrate_value_mismatch", f"Источник: текущая ставка {now}%, значение {old}% было предыдущим", location)]
    return []


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
        issues += _language_issues(claim, loc)
        issues += _keyrate_issues(claim, texts, loc)
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
