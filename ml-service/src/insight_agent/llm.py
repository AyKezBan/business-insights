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


class BudgetExceeded(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


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
    def __init__(self, limit_tokens: int):
        self.limit = limit_tokens
        self._day = date.today()
        self._used = 0
        self._lock = threading.Lock()

    def reserve(self, tokens: int) -> None:
        with self._lock:
            if date.today() != self._day:
                self._day, self._used = date.today(), 0
            if self._used + tokens > self.limit:
                raise BudgetExceeded("Исчерпан дневной лимит токенов")

    def add(self, tokens: int) -> None:
        with self._lock:
            self._used += tokens

    @property
    def used(self) -> int:
        return self._used


class RunBudget:
    def __init__(self, limit_tokens: int, daily: DailyBudget):
        self.limit = limit_tokens
        self.daily = daily
        self.used = 0

    def check(self, estimate: int) -> None:
        if self.used + estimate > self.limit:
            raise BudgetExceeded("Исчерпан лимит токенов на один запуск агента")
        self.daily.reserve(estimate)

    def add(self, tokens: int) -> None:
        self.used += tokens
        self.daily.add(tokens)


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

    def generate(self, step: str, model: str, messages: list[dict], schema: type[T], retries: int = 1) -> T:
        ensure_allowed(model)
        attempt_messages = list(messages)
        last_error = ""
        models = [model] + ([self.fallback_model] if self.fallback_model and self.fallback_model != model else [])
        for current in models:
            for attempt in range(retries + 1):
                self.budget.check(_estimate_tokens(attempt_messages))
                started = time.monotonic()
                try:
                    resp = self.client.chat(current, attempt_messages, json_mode=True)
                except (LLMError, httpx.HTTPError) as exc:
                    last_error = str(exc)
                    self._trace(step, current, attempt_messages, None, error=last_error, started=started)
                    break
                self.budget.add(resp.total_tokens)
                if current not in self.models_used:
                    self.models_used.append(current)
                try:
                    result = schema.model_validate(extract_json(resp.text))
                except (ValueError, ValidationError) as exc:
                    last_error = str(exc)[:800]
                    self._trace(step, current, attempt_messages, resp, error=last_error, started=started)
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
                self._trace(step, current, attempt_messages, resp, started=started)
                return result
        raise LLMError(f"Шаг {step}: не удалось получить корректный ответ ({last_error})")

    def _trace(self, step, model, messages, resp: LLMResponse | None, *, started: float, error: str | None = None):
        prompt = "\n".join(m["content"] for m in messages)
        self.tracer.emit(
            "llm_call",
            step,
            {
                "model": model,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
                "prompt_chars": len(prompt),
                "prompt_tokens": resp.prompt_tokens if resp else None,
                "completion_tokens": resp.completion_tokens if resp else None,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": error,
            },
        )
