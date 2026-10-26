import pytest

from fakes import FIXTURES, analysis_reply, draft_reply
from insight_agent.agent.critic import _numbers, check, has_errors
from insight_agent.agent.state import AgentState
from insight_agent.schemas import EventAnalysis, PersonalizationDraft
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.profile import derive_signals, missing_profile_data


def _state(request_, with_bank=True) -> AgentState:
    state = AgentState(
        run_id="run_critic",
        request=request_,
        signals=derive_signals(request_.profile),
        missing_profile=missing_profile_data(request_.profile),
        analysis=EventAnalysis.model_validate(analysis_reply()),
    )
    if with_bank:
        for frag in BankIndex.from_jsonl(FIXTURES / "bank_fragments.jsonl").fragments:
            state.bank[frag.fragment_id] = frag
    return state


def _codes(issues, severity="error"):
    return {i.code for i in issues if i.severity == severity}


def _draft(**changes) -> PersonalizationDraft:
    data = draft_reply()
    data.update(changes)
    return PersonalizationDraft.model_validate(data)


def test_grounded_draft_passes(request_):
    issues = check(_state(request_), _draft())
    assert not has_errors(issues), issues


def test_unknown_source_and_feature_are_errors(request_):
    data = draft_reply()
    data["why_it_matters"][0]["source_ids"] = ["news_invented"]
    data["why_it_matters"][0]["profile_features"] = ["products.crypto"]
    codes = _codes(check(_state(request_), PersonalizationDraft.model_validate(data)))
    assert {"unknown_source", "unknown_feature"} <= codes


def test_bank_view_must_cite_bank_material(request_):
    data = draft_reply()
    data["bank_view"][0]["source_ids"] = ["news_demo_rate"]
    assert "bank_view_without_bank_source" in _codes(check(_state(request_), PersonalizationDraft.model_validate(data)))


def test_model_inference_cannot_sit_in_bank_view(request_):
    data = draft_reply()
    data["bank_view"][0]["kind"] = "inference"
    assert "bank_view_kind" in _codes(check(_state(request_), PersonalizationDraft.model_validate(data)))


def test_invented_number_in_fact_is_error(request_):
    issues = check(_state(request_), PersonalizationDraft.model_validate(draft_reply(bad_number=True)))
    assert "unsupported_number" in _codes(issues)


def test_retelling_without_company_features_is_rejected(request_):
    data = draft_reply()
    for claim in data["why_it_matters"] + data["consequences"] + data["risks"] + data["actions"]:
        claim["profile_features"] = []
    data["opportunities"] = []
    codes = _codes(check(_state(request_), PersonalizationDraft.model_validate(data)))
    assert {"not_personalized", "single_factor"} <= codes


def test_empty_sections_are_errors(request_):
    issues = check(_state(request_), _draft(actions=[], risks=[], opportunities=[]))
    assert "missing_section" in _codes(issues)


def test_unused_bank_material_is_warning(request_):
    issues = check(_state(request_), PersonalizationDraft.model_validate(draft_reply(with_bank=False)))
    assert "bank_material_unused" in _codes(issues, "warning")
    assert not has_errors(issues)


@pytest.mark.parametrize(
    "text",
    ["2 400 000 000 руб.", "2\u00a0400\u00a0000\u00a0000 руб.", "2,4 млрд руб.", "2.40 млрд", "2400 млн"],
)
def test_same_amount_written_differently_is_the_same_number(text):
    assert "2400000000" in _numbers(text)


def test_digit_groups_are_not_split_into_separate_numbers():
    assert _numbers("выручка 2 400 000 000 руб.") == {"2400000000"}


def test_unit_does_not_swallow_longer_words():
    assert _numbers("5 миллионов") == {"5"}


def test_digit_group_does_not_swallow_digits_of_the_next_number():
    assert _numbers("Q2 2027 forecast: the rate remains 18%.") == {"2", "2027", "18"}
    assert _numbers("1 2345") == {"1", "2345"}
