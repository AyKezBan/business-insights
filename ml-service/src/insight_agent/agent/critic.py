import re

from insight_agent.agent.state import AgentState
from insight_agent.schemas import CheckIssue, Claim, ClaimKind, PersonalizationDraft, SourceKind
from insight_agent.tools.search import tokenize

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
NEEDS_SOURCE = {ClaimKind.fact, ClaimKind.assessment}
NEEDS_BASIS = {ClaimKind.inference, ClaimKind.recommendation}
RETELL_OVERLAP = 0.6


def _norm(number: str) -> str:
    number = number.replace(",", ".")
    return number.rstrip("0").rstrip(".") if "." in number else number


def _numbers(text: str) -> set[str]:
    return {_norm(n) for n in _NUMBER.findall(text)}


def _claims(draft: PersonalizationDraft) -> list[tuple[str, Claim]]:
    sections = ["why_it_matters", "consequences", "risks", "opportunities", "actions", "bank_view"]
    return [(f"{name}[{i}]", c) for name in sections for i, c in enumerate(getattr(draft, name))]


def _overlap(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    return len(ta & tb) / len(ta) if ta else 0.0


def check(state: AgentState, draft: PersonalizationDraft) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    catalog = state.sources()
    texts = state.source_texts()
    features = state.signal_features
    profile_numbers = _numbers(state.profile_text())

    def err(code, msg, loc=None):
        issues.append(CheckIssue(code=code, severity="error", message=msg, location=loc))

    def warn(code, msg, loc=None):
        issues.append(CheckIssue(code=code, severity="warning", message=msg, location=loc))


    for name in ("why_it_matters", "consequences", "actions", "bank_view"):
        if not getattr(draft, name):
            err("missing_section", f"Раздел {name} пуст", name)
    if not draft.risks and not draft.opportunities:
        err("missing_section", "Нет ни рисков, ни возможностей", "risks")

    for loc, claim in _claims(draft):
        unknown = [s for s in claim.source_ids if s not in catalog]
        if unknown:
            err("unknown_source", f"Ссылки на несуществующие источники: {unknown}", loc)
        bad_features = [f for f in claim.profile_features if f not in features]
        if bad_features:
            err("unknown_feature", f"Признаков нет в профиле: {bad_features}", loc)
        if claim.kind in NEEDS_SOURCE and not claim.source_ids:
            err("no_source", "Факт или позиция банка без источника", loc)
        if claim.kind in NEEDS_BASIS and not claim.source_ids and not claim.profile_features:
            err("no_basis", "Вывод без источников и признаков компании", loc)


        cites_bank = any(catalog.get(s) and catalog[s].kind == SourceKind.bank for s in claim.source_ids)
        if claim.kind == ClaimKind.assessment:
            non_bank = [s for s in claim.source_ids if s in catalog and catalog[s].kind != SourceKind.bank]
            if not cites_bank or non_bank:
                err("bank_view_without_bank_source", "Позиция банка должна ссылаться только на материалы банка", loc)
        if loc.startswith("bank_view") and claim.kind not in (ClaimKind.assessment, ClaimKind.unavailable):
            err("bank_view_kind", "В разделе банка допустимы только assessment и unavailable", loc)


        cited = " ".join(texts.get(s, "") for s in claim.source_ids)
        known = _numbers(cited) | profile_numbers
        foreign = sorted(_numbers(claim.text) - known)
        if claim.kind in NEEDS_SOURCE and foreign:
            err("unsupported_number", f"Числа не найдены в источниках: {foreign}", loc)
        elif foreign := [n for n in foreign if len(n) > 1]:

            warn("unsupported_number", f"Числа не найдены в источниках: {foreign}", loc)

    if state.bank and all(c.kind == ClaimKind.unavailable for c in draft.bank_view):
        warn("bank_material_unused", "Материалы банка найдены, но не использованы", "bank_view")


    used = {f for _, c in _claims(draft) for f in c.profile_features}
    if not any(c.profile_features for c in draft.why_it_matters):
        err("not_personalized", "«Почему это важно» не опирается на признаки компании", "why_it_matters")
    if len(state.signals) >= 2 and len(used) < 2:
        err("single_factor", "Вывод учитывает меньше двух признаков компании", None)
    for i, c in enumerate(draft.consequences):
        if _overlap(c.text, state.event.text) > RETELL_OVERLAP:
            warn("retelling", "Последствие похоже на пересказ новости", f"consequences[{i}]")
    for i, c in enumerate(draft.actions):
        if not c.profile_features:
            warn("generic_action", "Действие не привязано к признакам компании", f"actions[{i}]")
    return issues


def has_errors(issues: list[CheckIssue]) -> bool:
    return any(i.severity == "error" for i in issues)
