import json
from collections.abc import Callable
from pathlib import Path

from insight_agent.llm import LLMError, LLMResponse

FIXTURES = Path(__file__).parent / "fixtures"

STEP_MARKERS = [
    ("revise", "Проверка нашла проблемы"),
    ("answer", "уточняющий вопрос"),
    ("analyze_event", "Разбери новость"),
    ("plan", "Ты планировщик"),
    ("personalize", "Объясни, как событие влияет"),
]

Reply = str | dict | Exception | Callable[[list[dict]], str | dict]


def detect_step(messages: list[dict]) -> str:
    system = next((m["content"] for m in messages if m.get("role") == "system"), "")
    if system.startswith("SEMANTIC_VERIFIER_V1."):
        return "semantic_verify"
    text = messages[0]["content"] + "\n" + messages[-1]["content"]
    for step, marker in STEP_MARKERS:
        if marker in text:
            return step
    raise AssertionError(f"Неизвестный шаг агента: {text[:200]}")


class ScriptedClient:
    def __init__(self, script: dict[str, list[Reply]]):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, str]] = []

    def chat(self, model: str, messages: list[dict], *, json_mode: bool) -> LLMResponse:
        step = detect_step(messages)
        self.calls.append((step, model))
        queue = self.script.get(step)
        if not queue:
            raise LLMError(f"Нет сценария для шага {step}")
        reply = queue.pop(0) if step == "semantic_verify" or len(queue) > 1 else queue[0]
        if callable(reply) and not isinstance(reply, Exception):
            reply = reply(messages)
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return LLMResponse(text=text, model=model, prompt_tokens=800, completion_tokens=400)

    def steps(self) -> list[str]:
        return [s for s, _ in self.calls]


def semantic_contents(draft: dict, *, answer: bool = False) -> list[dict]:
    """Normalize fixture content only; callers separately choose what was reviewed."""
    from insight_agent.schemas import PersonalizationDraft
    if answer:
        from insight_agent.agent.qa import AnswerDraft
        parsed = AnswerDraft.model_validate(draft)
        sections = ("claims",)
    else:
        parsed = PersonalizationDraft.model_validate(draft)
        sections = ("why_it_matters", "consequences", "risks", "opportunities", "actions", "bank_view")
    contents = []
    for section in sections:
        for claim in getattr(parsed, section):
            if claim.kind in ("fact", "assessment") and not getattr(claim, "horizon", None):
                continue
            contents.append(claim.model_dump(mode="json", exclude={"claim_id", "evidence_spans"}))
    contents.extend(s.model_dump(mode="json") for s in getattr(parsed, "scenarios", []))
    contents.extend({"missing_data": text} for text in parsed.missing_data)
    return contents


def reviewed_semantics(supported_contents: list[dict]):
    """Finite scripts explicitly whitelist exact reviewed content, never generated IDs."""
    canonical = lambda content: json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    reviewed = frozenset(canonical(content) for content in supported_contents)

    def respond(messages):
        payload = json.loads(messages[-1]["content"])
        verdicts = []
        for candidate in payload["candidates"]:
            supported = canonical(candidate["content"]) in reviewed
            declared = candidate["content"].get("source_ids", [])
            span_ids = [span["span_id"] for evidence in payload["evidence"]
                        if evidence["source"]["source_id"] in declared for span in evidence["spans"]] if supported else []
            verdicts.append({"claim_id": candidate["claim_id"], "status": "supported" if supported else "unsupported",
                             "reason": "Explicitly reviewed fixture content" if supported else "Content not reviewed by this fixture",
                             "span_ids": span_ids})
        return {"verdicts": verdicts}

    return respond


def analysis_reply() -> dict:
    return {
        "event_id": "evt_demo_rate",
        "summary": "Банк России повысил ключевую ставку до 18% и допустил дальнейшее ужесточение.",
        "facts": [
            {"text": "Совет директоров Банка России повысил ключевую ставку на 100 б.п., до 18% годовых.", "kind": "fact", "source_ids": ["news_demo_rate"]},
            {"text": "Регулятор указал на сохраняющееся инфляционное давление и допустил дальнейшее ужесточение.", "kind": "fact", "source_ids": ["news_demo_rate"]},
        ],
        "actors": ["Банк России"],
        "impact_channels": ["cost_of_debt", "deposit_yield", "domestic_demand", "not_a_channel"],
        "bank_queries": ["ключевая ставка прогноз"],
        "open_questions": ["как изменятся ставки по корпоративным кредитам"],
    }


def plan_reply() -> dict:
    return {
        "steps": [
            {"tool": "search_bank_analytics", "args": {"query": "ключевая ставка прогноз"}, "purpose": "позиция банка"},
            {"tool": "search_news", "args": {"query": "ставки по корпоративным кредитам"}, "purpose": "контекст"},
            {"tool": "delete_everything", "args": {}, "purpose": "инструмент не из белого списка"},
        ]
    }


def draft_reply(*, with_bank: bool = True, bad_number: bool = False) -> dict:
    bank_view = (
        [
            {
                "text": "Синтетический фрагмент для тестов. Аналитики ожидают, что ключевая ставка останется на уровне 18% до конца 2026 года, а снижение начнётся не раньше второго квартала 2027 года. Стоимость заёмного финансирования для компаний будет оставаться высокой.",
                "kind": "assessment",
                "source_ids": ["bank_demo_rate_p3"],
            }
        ]
        if with_bank
        else [{"text": "Материалов банка по теме нет.", "kind": "unavailable"}]
    )
    fact_text = (
        "Ставка повышена до 21% годовых." if bad_number else "Совет директоров Банка России повысил ключевую ставку на 100 б.п., до 18% годовых."
    )
    return {
        "headline": "Проверка условий финансирования после изменения ключевой ставки",
        "why_it_matters": [
            {
                "text": "Если у компании есть кредиты или лизинг, проверьте условия договоров: связь договорной ставки с ключевой пока неизвестна.",
                "kind": "inference",
                "source_ids": ["news_demo_rate"],
                "profile_features": ["products.loans", "products.leasing"],
            },
            {"text": fact_text, "kind": "fact", "source_ids": ["news_demo_rate"]},
        ],
        "consequences": [
            {
                "text": "Если финансирование оборотного капитала подорожает, компании может потребоваться пересмотреть закупки оборудования.",
                "kind": "inference",
                "source_ids": ["news_demo_rate"],
                "profile_features": ["okved_code"],
            }
        ],
        "risks": [
            {
                "text": "Если в договорах есть ставка, привязанная к ключевой, её рост может увеличить процентные расходы; наличие такой привязки нужно проверить.",
                "kind": "inference",
                "source_ids": ["news_demo_rate"],
                "profile_features": ["products.loans"],
                "level": "high",
                "horizon": None,
            }
        ],
        "opportunities": [
            {
                "text": "При наличии свободных средств сравните доступные условия размещения; рост доходности не гарантирован.",
                "kind": "hypothesis",
                "profile_features": ["revenue_rub"],
                "level": "low",
            }
        ],
        "scenarios": [
            {
                "name": "Ставка сохраняется",
                "trigger": "Если регулятор сохранит ключевую ставку",
                "implication": "Условия действующих кредитов зависят от договоров; проверьте фиксированную или плавающую ставку.",
                "plausibility": "medium",
                "source_ids": ["bank_demo_rate_p3"] if with_bank else [],
            }
        ],
        "actions": [
            {
                "text": "Проверить график платежей по кредитам и лизингу и договорные условия изменения ставки.",
                "kind": "recommendation",
                "source_ids": ["news_demo_rate"],
                "profile_features": ["products.loans", "products.leasing"],
            }
        ],
        "bank_view": bank_view,
        "model_vs_bank": [],
        "impact_level": "high",
        "impact_rationale": "Связь условий финансирования с ключевой ставкой требует проверки договоров.",
        "missing_data": ["объём и тип ставки по кредитам"],
    }
