import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from decimal import Decimal
from uuid import uuid4

from insight_agent.budget import (
    BudgetExceeded,
    DailyBudgetExceeded,
    PriceTable,
    Reservation,
    RubleLedger,
    RunBudgetExceeded,
    RunTimeout,
)
from insight_agent.trace import Tracer

ALLOWED_MODEL_MARKERS = (
    "gemma426ba4b",
    "glm53flash",
    "qwen3635ba3b",
    "deepseekv41flash",
    "qwen3vl30ba3binstruct",
    "kimik26",
)

T = TypeVar("T", bound=BaseModel)


class ModelNotAllowed(ValueError):
    pass


class LLMError(RuntimeError):
    pass


def error_code(exc: Exception) -> str:
    if isinstance(exc, RunTimeout):
        return "timeout"
    if isinstance(exc, DailyBudgetExceeded):
        return "budget_daily"
    if isinstance(exc, RunBudgetExceeded):
        return "budget_run"
    return "llm_error"


def _normalize(model: str) -> str:
    return re.sub(r"[^a-z0-9]", "", model.lower())


def ensure_allowed(model: str) -> str:
    if not any(marker in _normalize(model) for marker in ALLOWED_MODEL_MARKERS):
        raise ModelNotAllowed(f"Модель {model!r} не входит в список разрешённых кейсом")
    return model


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ChatClient(Protocol):
    def chat(self, model: str, messages: list[dict], *, json_mode: bool) -> LLMResponse: ...


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def chat(self, model: str, messages: list[dict], *, json_mode: bool) -> LLMResponse:
        body: dict = {"model": model, "messages": messages, "temperature": 0.2}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = self._http.post("/chat/completions", json=body)
        if resp.status_code == 400 and json_mode:

            body.pop("response_format")
            resp = self._http.post("/chat/completions", json=body)
        if resp.status_code >= 400:
            raise LLMError(f"LLM вернул {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        usage = payload.get("usage") or {}
        return LLMResponse(
            text=payload["choices"][0]["message"]["content"] or "",
            model=payload.get("model", model),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
        )


class DailyBudget:
    """Суточный лимит: токены (в памяти) и, если задан ledger, рубли (в файле)."""

    def __init__(self, limit_tokens: int, ledger: RubleLedger | None = None, prices: PriceTable | None = None):
        self.limit = limit_tokens
        self.ledger = ledger
        self.prices = prices or PriceTable()
        self._day = date.today()
        self._used = 0
        self._lock = threading.Lock()

    def reserve(self, tokens: int, model: str | None = None) -> Reservation | None:
        with self._lock:
            if date.today() != self._day:
                self._day, self._used = date.today(), 0
            if self._used + tokens > self.limit:
                raise DailyBudgetExceeded("Исчерпан дневной лимит токенов")
        if self.ledger and model:
            return self.ledger.reserve(self.prices.upper_bound(model, tokens))
        return None

    def add(self, tokens: int, *, reservation: Reservation | None = None, model: str | None = None,
            prompt_tokens: int = 0, completion_tokens: int = 0) -> Decimal | None:
        with self._lock:
            self._used += tokens
        if self.ledger and model:
            cost = self.prices.cost(model, prompt_tokens, completion_tokens)
            self.ledger.settle(reservation, cost)
            return cost
        return None

    def release(self, reservation: Reservation | None) -> None:
        if self.ledger:
            self.ledger.release(reservation)

    def optional_allowed(self) -> bool:
        return self.ledger is None or self.ledger.level() != "stop"

    @property
    def used(self) -> int:
        return self._used


class RunBudget:
    def __init__(self, limit_tokens: int, daily: DailyBudget, deadline: float | None = None):
        self.limit = limit_tokens
        self.daily = daily
        self.deadline = deadline
        self.used = 0
        self.calls = 0
        self.cost = Decimal(0)
        self._reservation: Reservation | None = None

    def check(self, estimate: int, model: str | None = None) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise RunTimeout("Исчерпано время на один запуск агента")
        if self.used + estimate > self.limit:
            raise RunBudgetExceeded("Исчерпан лимит токенов на один запуск агента")
        self._reservation = self.daily.reserve(estimate, model)

    def add(self, tokens: int, *, model: str | None = None, prompt_tokens: int = 0,
            completion_tokens: int = 0) -> Decimal | None:
        cost = self.daily.add(tokens, reservation=self._reservation, model=model,
                              prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        self._reservation = None
        self.used += tokens
        self.calls += 1
        if cost is not None:
            self.cost += cost
        return cost

    def check_terminal(self) -> None:
        """Validate incurred usage after settlement without reserving another call."""
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise RunTimeout("Исчерпано время на один запуск агента")
        if self.used > self.limit:
            raise RunBudgetExceeded("Исчерпан лимит токенов на один запуск агента")
        if self.daily.used > self.daily.limit:
            raise DailyBudgetExceeded("Исчерпан дневной лимит токенов")

    def release(self) -> None:
        self.daily.release(self._reservation)
        self._reservation = None

    def optional_allowed(self) -> bool:
        return self.daily.optional_allowed()


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    fenced = _JSON_FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("В ответе нет JSON-объекта")
    return json.loads(candidate[start : end + 1])


def _estimate_tokens(messages: list[dict]) -> int:

    return sum(len(m["content"]) for m in messages) // 3 + 1500


class StructuredLLM:
    def __init__(self, client: ChatClient, budget: RunBudget, tracer: Tracer, fallback_model: str | None = None):
        self.client = client
        self.budget = budget
        self.tracer = tracer
        self.fallback_model = ensure_allowed(fallback_model) if fallback_model else None
        self.models_used: list[str] = []
        self._started = time.monotonic()

    def usage(self) -> dict:
        return {
            "tokens": self.budget.used,
            "calls": self.budget.calls,
            "cost_rub": str(self.budget.cost),
            "duration_ms": int((time.monotonic() - self._started) * 1000),
        }

    def generate(self, step: str, model: str, messages: list[dict], schema: type[T], retries: int = 1) -> T:
        ensure_allowed(model)
        attempt_messages = list(messages)
        last_error = ""
        models = [model] + ([self.fallback_model] if self.fallback_model and self.fallback_model != model else [])
        for current in models:
            for attempt in range(retries + 1):
                self.budget.check(_estimate_tokens(attempt_messages), current)
                started = time.monotonic()
                try:
                    resp = self.client.chat(current, attempt_messages, json_mode=True)
                except (LLMError, httpx.HTTPError) as exc:
                    self.budget.release()
                    last_error = str(exc)
                    self._trace(step, current, attempt_messages, None, error=last_error, started=started)
                    break
                cost = self.budget.add(resp.total_tokens, model=current, prompt_tokens=resp.prompt_tokens,
                                       completion_tokens=resp.completion_tokens)
                if current not in self.models_used:
                    self.models_used.append(current)
                try:
                    self.budget.check_terminal()
                except BudgetExceeded as exc:
                    self._trace(step, current, attempt_messages, resp, error=str(exc), started=started, cost=cost)
                    raise
                try:
                    result = schema.model_validate(extract_json(resp.text))
                except (ValueError, ValidationError) as exc:
                    last_error = str(exc)[:800]
                    self._trace(step, current, attempt_messages, resp, error=last_error, started=started, cost=cost)
                    attempt_messages = messages + [
                        {"role": "assistant", "content": resp.text},
                        {
                            "role": "user",
                            "content": "Ответ не соответствует схеме. Ошибки:\n"
                            + last_error
                            + "\nВерни только исправленный JSON-объект.",
                        },
                    ]
                    continue
                self._trace(step, current, attempt_messages, resp, started=started, cost=cost)
                return result
        raise LLMError(f"Шаг {step}: не удалось получить корректный ответ ({last_error})")

    def _trace(self, step, model, messages, resp: LLMResponse | None, *, started: float, error: str | None = None,
               cost: Decimal | None = None):
        prompt = "\n".join(m["content"] for m in messages)
        self.tracer.emit(
            "llm_call",
            step,
            {
                "call_id": uuid4().hex,
                "model": model,
                "cost_rub": str(cost) if cost is not None else None,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
                "prompt_chars": len(prompt),
                "prompt_tokens": resp.prompt_tokens if resp else None,
                "completion_tokens": resp.completion_tokens if resp else None,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": error,
            },
        )
