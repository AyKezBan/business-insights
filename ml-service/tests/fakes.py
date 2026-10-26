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
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(reply) and not isinstance(reply, Exception):
            reply = reply(messages)
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return LLMResponse(text=text, model=model, prompt_tokens=800, completion_tokens=400)

    def steps(self) -> list[str]:
        return [s for s, _ in self.calls]


def analysis_reply() -> dict:
    return {
        "event_id": "evt_demo_rate",
        "summary": "Банк России повысил ключевую ставку до 18% и допустил дальнейшее ужесточение.",
        "facts": [
            {"text": "Ключевая ставка повышена на 100 б.п., до 18% годовых.", "kind": "fact", "source_ids": ["news_demo_rate"]},
            {"text": "Регулятор допустил дальнейшее ужесточение политики.", "kind": "fact", "source_ids": ["news_demo_rate"]},
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
                "text": "Аналитики банка ожидают, что ставка останется на уровне 18% до конца 2026 года.",
                "kind": "assessment",
                "source_ids": ["bank_demo_rate_p3"],
            }
        ]
        if with_bank
        else [{"text": "Материалов банка по теме нет.", "kind": "unavailable"}]
    )
    fact_text = (
        "Ставка повышена до 21% годовых." if bad_number else "Ставка повышена до 18% годовых."
    )
    return {
        "headline": "Рост ставки удорожает кредиты и лизинг компании",
        "why_it_matters": [
            {
                "text": "У компании есть кредиты и лизинг в банке, поэтому рост ставки напрямую повышает стоимость обслуживания долга.",
                "kind": "inference",
                "source_ids": ["news_demo_rate"],
                "profile_features": ["products.loans", "products.leasing"],
            },
            {"text": fact_text, "kind": "fact", "source_ids": ["news_demo_rate"]},
        ],
        "consequences": [
            {
                "text": "Металлообработка с ростом затрат на финансирование оборотного капитала может отложить закупки оборудования.",
                "kind": "inference",
                "source_ids": ["news_demo_rate"],
                "profile_features": ["okved_code"],
            }
        ],
        "risks": [
            {
                "text": "Рост процентных расходов по плавающим кредитам.",
                "kind": "inference",
                "source_ids": ["news_demo_rate"],
                "profile_features": ["products.loans"],
                "level": "high",
                "horizon": "до 3 месяцев",
            }
        ],
        "opportunities": [
            {
                "text": "Если появятся свободные средства, их размещение станет доходнее.",
                "kind": "hypothesis",
                "profile_features": ["revenue_rub"],
                "level": "low",
            }
        ],
        "scenarios": [
            {
                "name": "Ставка сохраняется",
                "trigger": "Регулятор не меняет ставку на следующем заседании",
                "implication": "Стоимость долга остаётся высокой до 2027 года",
                "plausibility": "medium",
                "source_ids": ["bank_demo_rate_p3"] if with_bank else [],
            }
        ],
        "actions": [
            {
                "text": "Пересчитать график платежей по кредитам и лизингу при ставке 18%.",
                "kind": "recommendation",
                "source_ids": ["news_demo_rate"],
                "profile_features": ["products.loans", "products.leasing"],
            }
        ],
        "bank_view": bank_view,
        "model_vs_bank": [],
        "impact_level": "high",
        "impact_rationale": "Кредиты и лизинг делают компанию чувствительной к ставке.",
        "missing_data": ["объём и тип ставки по кредитам"],
    }
