"""Synthetic HTTP boundary regressions; no provider network or credentials."""

import json
import traceback
from collections import deque
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from insight_agent.budget import Price, PriceTable, RubleLedger, RunBudgetExceeded
from insight_agent.llm import DailyBudget, LLMError, OpenAICompatibleClient, RunBudget, StructuredLLM
from insight_agent.trace import Tracer, load_trace

MODEL = "gemma-4-26b-a4b"
SECRET = "synthetic-provider-key-do-not-expose"
PRIVATE = "synthetic-private-source-or-user-input"
MESSAGES = [{"role": "user", "content": "abc"}]
USAGE = {"prompt_tokens": 80, "completion_tokens": 40}


class Answer(BaseModel):
    ok: bool


def envelope(**changes):
    return {"model": MODEL, "choices": [{"message": {"content": '{"ok":true}'}}],
            "usage": USAGE, **changes}


@pytest.fixture
def harness(tmp_path):
    clients = []

    def make(replies, *, run_limit=40_000, input_price="0.5", output_price="1.5", fallback=None):
        replies = deque(replies)
        requests = []

        def respond(request):
            requests.append(request)
            reply = replies.popleft()
            if isinstance(reply, BaseException):
                raise reply
            return reply

        client = OpenAICompatibleClient("https://provider.invalid", SECRET)
        client._http.close()
        client._http = httpx.Client(base_url="https://provider.invalid", transport=httpx.MockTransport(respond))
        clients.append(client)
        ledger = RubleLedger(state_path=tmp_path / "ledger.json")
        prices = PriceTable(default=Price(Decimal(input_price), Decimal(output_price)))
        daily = DailyBudget(1_000_000, ledger, prices)
        budget = RunBudget(run_limit, daily)
        tracer = Tracer("run_boundary", tmp_path / "traces")
        llm = StructuredLLM(client, budget, tracer, fallback)
        return SimpleNamespace(client=client, llm=llm, budget=budget, daily=daily, ledger=ledger,
                               tracer=tracer, requests=requests, trace_dir=tmp_path / "traces")

    yield make
    for client in clients:
        client._http.close()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_error_body_never_reaches_exception_or_persisted_trace(harness, status):
    env = harness([httpx.Response(status, json={"error": f"{SECRET} {PRIVATE}"})])
    with pytest.raises(LLMError) as caught:
        env.llm.generate("check", MODEL, MESSAGES, Answer)
    rendered = "".join(traceback.format_exception(caught.value))
    trace = json.dumps([e.model_dump(mode="json") for e in load_trace(env.trace_dir, "run_boundary")])
    assert SECRET not in rendered + trace
    assert PRIVATE not in rendered + trace
    assert str(status) in rendered + trace
    assert env.ledger._inflight == 0


@pytest.mark.parametrize("invalid", [SECRET, PRIVATE, {SECRET: PRIVATE}])
def test_schema_diagnostics_omit_raw_values_and_dynamic_keys(harness, invalid):
    env = harness([httpx.Response(200, json=envelope(choices=[{"message": {"content": json.dumps({"ok": invalid})}}]))])
    with pytest.raises(LLMError) as caught:
        env.llm.generate("check", MODEL, MESSAGES, Answer, retries=0)
    rendered = "".join(traceback.format_exception(caught.value))
    trace = json.dumps([e.model_dump(mode="json") for e in load_trace(env.trace_dir, "run_boundary")])
    assert SECRET not in rendered + trace
    assert PRIVATE not in rendered + trace
    assert env.budget.used == 120
    assert env.ledger.spent == Decimal("0.1000")


def test_configured_secret_is_redacted_before_schema_repair_feedback(harness):
    env = harness([
        httpx.Response(200, json=envelope(choices=[{"message": {"content": json.dumps({"ok": SECRET})}}])),
        httpx.Response(200, json=envelope()),
    ])
    assert env.llm.generate("check", MODEL, MESSAGES, Answer).ok is True
    assert SECRET not in env.requests[1].content.decode()
    assert env.budget.used == 240


def test_configured_secret_is_redacted_in_direct_client_response(harness):
    env = harness([httpx.Response(200, json=envelope(choices=[{"message": {"content": SECRET}}]))])
    result = env.client.chat(MODEL, MESSAGES, json_mode=True)
    assert SECRET not in result.text
    assert (result.prompt_tokens, result.completion_tokens) == (80, 40)


@pytest.mark.parametrize("encoded_secret", [
    r"\u0073ynthetic-provider-key-do-not-expose",
    "".join(r"\u%04x" % ord(char) for char in SECRET),
])
@pytest.mark.parametrize("wrapper", ["plain", "fenced", "quoted-prefix"])
def test_escaped_secret_is_redacted_in_decoded_nested_values_and_keys(harness, encoded_secret, wrapper):
    class TextAnswer(BaseModel):
        value: str
        nested: list[dict[str, str]]

    content = ('{"value":"prefix ' + encoded_secret + ' suffix","nested":[{"'
               + encoded_secret + '":"' + encoded_secret + '"}]}')
    if wrapper == "fenced":
        content = "```json\n" + content + "\n```"
    elif wrapper == "quoted-prefix":
        content = 'A quoted " explanation before the object: ' + content
    env = harness([httpx.Response(200, json=envelope(choices=[{"message": {"content": content}}]))])
    result = env.llm.generate("check", MODEL, MESSAGES, TextAnswer)
    assert SECRET not in result.model_dump_json()
    assert result.value == "prefix [REDACTED] suffix"
    assert result.nested == [{"[REDACTED]": "[REDACTED]"}]
    assert env.budget.used == 120
    assert env.ledger.spent == Decimal("0.1000")


@pytest.mark.parametrize("suffix", ["}", ",}"])
def test_escaped_secret_is_redacted_before_schema_or_json_repair(harness, suffix):
    encoded_secret = r"\u0073ynthetic-provider-key-do-not-expose"
    content = '{"ok":"' + encoded_secret + '"' + suffix
    env = harness([
        httpx.Response(200, json=envelope(choices=[{"message": {"content": content}}])),
        httpx.Response(200, json=envelope()),
    ])
    assert env.llm.generate("check", MODEL, MESSAGES, Answer).ok is True
    repair_messages = json.loads(env.requests[1].content)["messages"]
    assistant = next(message["content"] for message in repair_messages if message["role"] == "assistant")
    assert encoded_secret not in assistant
    assert SECRET not in assistant
    assert "[REDACTED]" in assistant
    assert env.budget.used == 240


@pytest.mark.parametrize("payload", [
    envelope(choices=[]), envelope(choices=None), envelope(choices={}), envelope(choices=[None]),
    envelope(choices=[{"message": None}]), envelope(choices=[{"message": {"content": 7}}]),
    envelope(choices=[{"message": {"content": None}}]), envelope(model=[]),
])
def test_malformed_envelope_preserves_trustworthy_usage_and_allows_next_call(harness, payload):
    env = harness([httpx.Response(200, json=payload), httpx.Response(200, json=envelope())])
    with pytest.raises(LLMError):
        env.llm.generate("check", MODEL, MESSAGES, Answer)
    assert env.ledger._inflight == 0
    assert env.budget.used == env.daily.used == 120
    assert env.ledger.spent == env.budget.cost == Decimal("0.1000")
    assert env.tracer.events[-1].data["prompt_tokens"] == 80
    assert env.tracer.events[-1].data["completion_tokens"] == 40
    assert env.llm.generate("check", MODEL, MESSAGES, Answer).ok is True
    assert env.budget.used == 240
    assert env.budget.calls == env.ledger.calls == 2
    assert env.ledger._inflight == 0


@pytest.mark.parametrize("reply", [
    httpx.Response(200, text=f"not JSON {SECRET} {PRIVATE}"),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, json={k: v for k, v in envelope().items() if k != "usage"}),
    *[httpx.Response(200, json=envelope(usage=usage)) for usage in [
        None, [], {}, {"prompt_tokens": 80}, {"prompt_tokens": -1, "completion_tokens": 40},
        {"prompt_tokens": 80, "completion_tokens": -1}, {"prompt_tokens": "80", "completion_tokens": 40},
        {"prompt_tokens": True, "completion_tokens": 40}, {"prompt_tokens": 80, "completion_tokens": 1.5},
    ]],
    RuntimeError(f"unexpected client exception {SECRET} {PRIVATE}"),
])
@pytest.mark.parametrize("input_price,output_price", [("0.5", "1.5"), ("1.5", "0.5")])
def test_unknown_usage_is_explicit_conservative_charge_not_zero_or_stranded(harness, reply, input_price, output_price):
    env = harness([reply, httpx.Response(200, json=envelope())], input_price=input_price, output_price=output_price)
    with pytest.raises(LLMError) as caught:
        env.llm.generate("check", MODEL, MESSAGES, Answer)
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert env.ledger._inflight == 0
    assert env.budget.used == env.daily.used == 1501
    assert env.ledger.spent == env.budget.cost == Decimal("2.2515")
    assert env.llm.usage()["estimated_tokens"] == 1501
    event = env.tracer.events[-1].data
    assert event["usage_accounting"] == "reserved_estimate"
    assert event["prompt_tokens"] is None and event["completion_tokens"] is None
    assert event["estimated_tokens"] == 1501
    assert SECRET not in json.dumps(event) and PRIVATE not in json.dumps(event)
    assert env.llm.generate("check", MODEL, MESSAGES, Answer).ok is True
    assert env.budget.calls == env.ledger.calls == 2
    assert env.budget.used == 1621
    assert env.ledger._inflight == 0


def test_malformed_response_actual_overrun_stops_before_fallback(harness):
    env = harness([httpx.Response(200, json=envelope(choices=[], usage={"prompt_tokens": 800, "completion_tokens": 1400}))],
                  run_limit=2000, fallback="glm-5.3-flash")
    with pytest.raises(RunBudgetExceeded):
        env.llm.generate("check", MODEL, MESSAGES, Answer, retries=2)
    assert len(env.requests) == 1
    assert env.budget.used == 2200
    assert env.ledger.spent == Decimal("2.5000")
    assert env.ledger._inflight == 0


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_process_interrupt_is_not_swallowed_and_releases_reservation(harness, interrupt):
    env = harness([interrupt()])
    with pytest.raises(interrupt):
        env.llm.generate("check", MODEL, MESSAGES, Answer)
    assert env.ledger._inflight == 0
