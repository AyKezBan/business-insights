import pytest
from pydantic import BaseModel

from fakes import FIXTURES
from insight_agent.schemas import CompanyProfile
from insight_agent.tools.bank import BankIndex, BankSearchArgs
from insight_agent.tools.base import Tool, ToolError, ToolRegistry
from insight_agent.tools.profile import derive_signals, missing_profile_data
from insight_agent.trace import Tracer


class EchoArgs(BaseModel):
    text: str


def _registry(max_calls=2):
    tracer = Tracer("run_tools")
    tool = Tool("echo", "повтор", EchoArgs, lambda a: a.text, lambda r: {"len": len(r)})
    return ToolRegistry([tool], tracer, max_calls), tracer


def test_registry_rejects_unknown_tool_and_logs_it():
    tools, tracer = _registry()
    with pytest.raises(ToolError):
        tools.call("rm", {}, step="t")
    assert tracer.events[-1].data["rejected"] == "unknown_tool"


def test_registry_validates_args_and_limits_calls():
    tools, tracer = _registry(max_calls=1)
    with pytest.raises(ToolError):
        tools.call("echo", {"text": None}, step="t")
    assert tools.call("echo", {"text": "ok"}, step="t") == "ok"
    with pytest.raises(ToolError):
        tools.call("echo", {"text": "ещё"}, step="t")
    assert [e.data.get("rejected") for e in tracer.events] == ["bad_args", None, "call_limit"]


def test_bank_search_finds_rate_fragment_and_respects_date():
    index = BankIndex.from_jsonl(FIXTURES / "bank_fragments.jsonl")
    hits = index.search(BankSearchArgs(query="прогноз ключевой ставки"))
    assert hits[0].fragment.fragment_id == "bank_demo_rate_p3"
    assert index.search(BankSearchArgs(query="ключевая ставка", not_before="2026-10-01")) == []


def test_missing_bank_file_gives_empty_index(tmp_path):
    assert BankIndex.from_jsonl(tmp_path / "nope.jsonl").search(BankSearchArgs(query="ставка")) == []


def test_signals_link_products_to_channels(request_):
    signals = {s.feature: s for s in derive_signals(request_.profile)}
    assert "cost_of_debt" in signals["products.loans"].channels
    assert "products.deposits" not in signals
    assert signals["revenue_rub"].value == "2.4 млрд руб."
    assert "поставщик" in signals["counterparties[0]"].value


def test_revenue_placeholder_is_treated_as_missing():
    profile = CompanyProfile(company_id="U_X", revenue_rub=1000, okved_code="56.10")
    assert "revenue_rub" not in {s.feature for s in derive_signals(profile)}
    assert "revenue_rub" in missing_profile_data(profile)


def test_small_revenue_is_not_rounded_to_zero():
    profile = CompanyProfile(company_id="U_X", revenue_rub=136_493)
    signal = next(s for s in derive_signals(profile) if s.feature == "revenue_rub")
    assert signal.value == "136 493 руб."


def test_unknown_values_are_reported_as_missing():
    missing = missing_profile_data(CompanyProfile(company_id="U_X"))
    assert {"products.loans", "okved_code", "revenue_rub", "foreign_trade"} <= set(missing)
