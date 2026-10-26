import hashlib
from collections.abc import Callable
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


class _ProviderError(LLMError):
    """Controlled diagnostics plus trustworthy usage from an unusable reply."""

    def __init__(self, message: str, response: LLMResponse | None = None, *, rejected: bool = False):
        super().__init__(message)
        self.response = response
        self.rejected = rejected


def _valid_tokens(value: object) -> bool:
    return type(value) is int and value >= 0


_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def _redact_response_secret(text: str, secret: str) -> str:
    if not secret:
        return text

    def redact_value(value):
        if isinstance(value, str):
            return value.replace(secret, "[REDACTED]")
        if isinstance(value, list):
            return [redact_value(item) for item in value]
        if isinstance(value, dict):
            return {redact_value(key): redact_value(item) for key, item in value.items()}
        return value

    try:
        structured = extract_json(text)
    except ValueError:
        pass
    else:
        redacted = redact_value(structured)
        if redacted != structured:
            # Use the same parsed object as schema validation, regardless of fences
            # or surrounding prose. Preserve untouched responses byte-for-byte.
            text = json.dumps(redacted, ensure_ascii=False)

    def redact_string(match: re.Match) -> str:
        try:
            decoded = json.loads(match.group())
        except ValueError:
            return match.group()
        # Decode each JSON string (values AND keys), including Unicode escapes.
        # Token-wise handling also protects repair feedback for malformed objects.
        if secret in decoded:
            return json.dumps(decoded.replace(secret, "[REDACTED]"), ensure_ascii=False)
        return match.group()

    return _JSON_STRING.sub(redact_string, text).replace(secret, "[REDACTED]")


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0, max_output_tokens: int | None = 4096):
        self._api_key = api_key
        self._max_output_tokens = max_output_tokens
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def chat(self, model: str, messages: list[dict], *, json_mode: bool) -> LLMResponse:
        body: dict = {"model": model, "messages": messages, "temperature": 0.2}
        if self._max_output_tokens:
            body["max_tokens"] = self._max_output_tokens  # без предела модель может зациклиться и держать шлюз до таймаута
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            resp = self._http.post("/chat/completions", json=body)
            if resp.status_code == 400 and json_mode:
                body.pop("response_format")
                resp = self._http.post("/chat/completions", json=body)
        except Exception:
            # Transport exceptions can contain URLs, credentials and request data.
            raise _ProviderError("LLM: ошибка транспорта; расход неизвестен") from None
        if resp.status_code >= 400:
            # Upstream bodies are untrusted and never belong in public diagnostics.
            raise _ProviderError(f"LLM вернул HTTP {resp.status_code}", rejected=True)
        try:
            payload = resp.json()
        except ValueError:
            raise _ProviderError("LLM: некорректный JSON ответа; расход неизвестен") from None
        if not isinstance(payload, dict):
            raise _ProviderError("LLM: некорректная структура ответа; расход неизвестен")
        usage = payload.get("usage")
        if not isinstance(usage, dict) or not all(
            _valid_tokens(usage.get(key)) for key in ("prompt_tokens", "completion_tokens")
        ):
            raise _ProviderError("LLM: отсутствует достоверный расход токенов")
        incurred = LLMResponse("", model, usage["prompt_tokens"], usage["completion_tokens"])
        choices = payload.get("choices")
        if (not isinstance(choices, list) or not choices or not isinstance(choices[0], dict)
                or not isinstance(choices[0].get("message"), dict)
                or not isinstance(choices[0]["message"].get("content"), str)
                or not isinstance(payload.get("model", model), str)):
            raise _ProviderError("LLM: некорректная структура ответа", incurred)
        # Redact before validation, repair prompts, diagnostics or truncation.
        text = choices[0]["message"]["content"]
        returned_model = payload.get("model", model)
        if self._api_key:
            text = _redact_response_secret(text, self._api_key)
            returned_model = returned_model.replace(self._api_key, "[REDACTED]")
        return LLMResponse(
            text=text,
            model=returned_model,
            prompt_tokens=incurred.prompt_tokens,
            completion_tokens=incurred.completion_tokens,
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
            prompt_tokens: int = 0, completion_tokens: int = 0, estimated: bool = False) -> Decimal | None:
        with self._lock:
            self._used += tokens
        if self.ledger and model:
            cost = (self.prices.upper_bound(model, tokens) if estimated
                    else self.prices.cost(model, prompt_tokens, completion_tokens))
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
        self.estimated_tokens = 0
        self.estimated_cost = Decimal(0)
        self._reservation: Reservation | None = None

    def check(self, estimate: int, model: str | None = None) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise RunTimeout("Исчерпано время на один запуск агента")
        if self.used + estimate > self.limit:
            raise RunBudgetExceeded("Исчерпан лимит токенов на один запуск агента")
        self._reservation = self.daily.reserve(estimate, model)

    def add(self, tokens: int, *, model: str | None = None, prompt_tokens: int = 0,
            completion_tokens: int = 0, estimated: bool = False) -> Decimal | None:
        cost = self.daily.add(tokens, reservation=self._reservation, model=model,
                              prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, estimated=estimated)
        self._reservation = None
        self.used += tokens
        self.calls += 1
        if cost is not None:
            self.cost += cost
        if estimated:
            self.estimated_tokens += tokens
            self.estimated_cost += cost or Decimal(0)
        return cost

    def check_terminal(self) -> None:
        """Validate incurred usage after settlement without reserving another call."""
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise RunTimeout("Исчерпано время на один запуск агента")
        if self.used > self.limit:
            raise RunBudgetExceeded("Исчерпан лимит токенов на один запуск агента")
        if self.daily.used > self.daily.limit:
            raise DailyBudgetExceeded("Исчерпан дневной лимит токенов")
        if self.daily.ledger and self.daily.ledger.spent > self.daily.ledger.limit:
            raise DailyBudgetExceeded("Исчерпан дневной лимит стоимости ответов")

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


_SAFE_VALIDATION_CODES = frozenset({
    "missing", "string_type", "string_too_short", "string_too_long", "int_type", "int_parsing",
    "float_type", "float_parsing", "bool_type", "bool_parsing", "list_type", "dict_type",
    "model_type", "literal_error", "enum", "greater_than", "greater_than_equal", "less_than",
    "less_than_equal", "too_short", "too_long", "extra_forbidden", "finite_number",
})


def _validation_diagnostic(schema: type[BaseModel], exc: ValidationError) -> str:
    summary = f"Ответ не соответствует схеме (ошибок: {exc.error_count()})"
    try:
        root = schema.model_json_schema()
    except Exception:
        return summary

    def variants(nodes):
        pending, found, seen = list(nodes), [], set()
        while pending:
            node = pending.pop()
            if not isinstance(node, dict) or id(node) in seen:
                continue
            seen.add(id(node))
            found.append(node)
            ref = node.get("$ref", "")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref[len("#/$defs/"):].replace("~1", "/").replace("~0", "~")
                pending.append(root.get("$defs", {}).get(name))
            for keyword in ("anyOf", "oneOf", "allOf"):
                pending.extend(node.get(keyword, []))
        return found

    hints = []
    # Never render msg/input/context: custom validators can put raw text in all three.
    for error in exc.errors(include_input=False, include_context=False, include_url=False)[:5]:
        nodes, path = [root], ""
        for part in error["loc"][:6]:
            nodes = variants(nodes)
            items = [node["items"] for node in nodes if isinstance(node.get("items"), dict)]
            if type(part) is int and items:
                path += f"[{part}]" if 0 <= part <= 9999 else "[?]"
                nodes = items
                continue
            children = [node["properties"][part] for node in nodes
                        if isinstance(part, str) and part in node.get("properties", {})]
            # A map key is never a static field, even if another schema field shares its name.
            name = part if children and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,47}", part) else "[field]"
            path += ("." if path else "") + name
            nodes = children or [node.get("additionalProperties") for node in nodes]
        code = error["type"] if error["type"] in _SAFE_VALIDATION_CODES else "invalid"
        hints.append(f"{(path or '[root]')[:128]}:{code}")
    return summary + ": " + "; ".join(hints)


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
            "estimated_tokens": self.budget.estimated_tokens,
            "estimated_cost_rub": str(self.budget.estimated_cost),
            "duration_ms": int((time.monotonic() - self._started) * 1000),
        }

    def generate(self, step: str, model: str, messages: list[dict], schema: type[T], retries: int = 1,
                 normalize: Callable[[dict], dict] | None = None) -> T:
        ensure_allowed(model)
        attempt_messages = list(messages)
        last_error = ""
        models = [model] + ([self.fallback_model] if self.fallback_model and self.fallback_model != model else [])
        for current in models:
            for attempt in range(retries + 1):
                estimate = _estimate_tokens(attempt_messages)
                self.budget.check(estimate, current)
                started = time.monotonic()
                failure = None
                estimated_tokens = 0
                try:
                    resp = self.client.chat(current, attempt_messages, json_mode=True)
                    if (not isinstance(resp, LLMResponse) or not isinstance(resp.text, str)
                            or not _valid_tokens(resp.prompt_tokens) or not _valid_tokens(resp.completion_tokens)):
                        raise _ProviderError("LLM: некорректный ответ клиента; расход неизвестен")
                except _ProviderError as exc:
                    failure = str(exc)
                    resp = exc.response
                    if resp is None and not exc.rejected:
                        estimated_tokens = estimate
                except LLMError:
                    # Explicit client rejections retain their release/fallback contract.
                    failure, resp = "LLM: ошибка клиента", None
                except Exception:
                    failure, resp = "LLM: непредвиденная ошибка клиента; расход неизвестен", None
                    estimated_tokens = estimate
                except BaseException:
                    self.budget.release()
                    raise
                if failure and resp is None and not estimated_tokens:
                    self.budget.release()
                    last_error = failure
                    self._trace(step, current, attempt_messages, None, error=last_error, started=started)
                    break
                # Unknown usage consumes the full reservation at the higher configured
                # token price. It is an explicit estimate, never claimed as actual usage.
                cost = self.budget.add(estimated_tokens or resp.total_tokens, model=current,
                                       prompt_tokens=resp.prompt_tokens if resp else 0,
                                       completion_tokens=resp.completion_tokens if resp else 0,
                                       estimated=bool(estimated_tokens))
                if current not in self.models_used:
                    self.models_used.append(current)
                try:
                    self.budget.check_terminal()
                except BudgetExceeded as exc:
                    self._trace(step, current, attempt_messages, resp, error=str(exc), started=started, cost=cost,
                                estimated_tokens=estimated_tokens)
                    raise exc from None
                if failure:
                    last_error = failure
                    self._trace(step, current, attempt_messages, resp, error=last_error, started=started, cost=cost,
                                estimated_tokens=estimated_tokens)
                    break
                try:
                    data = extract_json(resp.text)
                    result = schema.model_validate(normalize(data) if normalize else data)
                except (ValueError, ValidationError) as exc:
                    last_error = (_validation_diagnostic(schema, exc)
                                  if isinstance(exc, ValidationError) else "Ответ не содержит корректный JSON-объект")
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
               cost: Decimal | None = None, estimated_tokens: int = 0):
        prompt = "\n".join(m["content"] for m in messages)
        self.tracer.emit(
            "llm_call",
            step,
            {
                "call_id": uuid4().hex,
                "model": model,
                "cost_rub": str(cost) if cost is not None else None,
                "usage_accounting": "reserved_estimate" if estimated_tokens else "reported" if resp else "unavailable",
                "estimated_tokens": estimated_tokens or None,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
                "prompt_chars": len(prompt),
                "prompt_tokens": resp.prompt_tokens if resp else None,
                "completion_tokens": resp.completion_tokens if resp else None,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": error,
            },
        )
