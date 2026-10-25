from decimal import Decimal

import pytest

from insight_agent.agent.critic import check_claims
from insight_agent.schemas import Claim, ClaimKind, CompanyProfile, Counterparty
from insight_agent.tools.profile import derive_signals, missing_profile_data
from test_critic import _state


def _signals(**fields):
    profile = CompanyProfile(company_id="U_X", **fields)
    return {s.feature: s for s in derive_signals(profile)}


def test_foreign_trade_flag_does_not_become_import_or_export():
    signal = _signals(foreign_trade=True, foreign_trade_countries=["China"])["foreign_trade"]
    assert {"import_costs", "export_demand"}.isdisjoint(signal.channels)
    assert {"fx", "sanctions"} <= set(signal.channels)
    assert "не означают импорт или экспорт" in signal.note


def test_unknown_foreign_trade_stays_unknown():
    assert "foreign_trade" not in _signals()
    assert "foreign_trade" in missing_profile_data(CompanyProfile(company_id="U_X"))


def test_transfers_to_self_are_not_a_counterparty_dependency():
    own = Counterparty(company_id="U_X", direction="outgoing", share_of_flow=0.8)
    other = Counterparty(company_id="U_Y", direction="outgoing", share_of_flow=0.1)
    signals = _signals(counterparties=[own, other], self_transfer_share=0.8)
    # индекс исходный: Backend сверяет counterparties[i] с отправленным списком
    assert [k for k in signals if k.startswith("counterparties")] == ["counterparties[1]"]
    assert "U_Y" in signals["counterparties[1]"].value
    assert "self_transfer_share" not in signals and "80%" in signals["counterparties[1]"].note


def test_counterparty_with_unknown_industry_is_not_guessed():
    cp = Counterparty(company_id="U_Z", direction="incoming", share_of_flow=0.3)
    value = _signals(counterparties=[cp])["counterparties[0]"]
    assert "отрасль неизвестна" in value.value and "не подтверждён" in value.note
    assert value.channels == ["counterparty_risk"]


def test_payment_link_is_not_called_supplier_or_buyer():
    cp = Counterparty(company_id="U_Z", direction="incoming", okved_name="Торговля")
    assert "покупатель" not in _signals(counterparties=[cp])["counterparties[0]"].value


def test_group_signal_says_role_of_members_is_unknown():
    note = _signals(group_member_okveds=["24.10"])["group_member_okveds"].note
    assert "не переносится" in note


def test_revenue_placeholder_is_unknown_in_signals_and_missing_list():
    profile = CompanyProfile(company_id="U_X", revenue_rub=Decimal(1000), okved_code="25.61")
    assert "revenue_rub" not in {s.feature for s in derive_signals(profile)}
    assert "revenue_rub" in missing_profile_data(profile)


def _codes(request_, claim, section="consequences"):
    return {i.code for i in check_claims([claim], _state(request_), section=section)}


@pytest.mark.parametrize(
    "text",
    [
        "Платёж по кредиту вырастет на 10% в следующем квартале.",
        "Процентные расходы по лизингу увеличатся.",
        "Ежемесячные выплаты по договору составят больше текущих.",
    ],
)
def test_payment_change_stated_as_fact_is_an_error(request_, text):
    claim = Claim(text=text.replace(" на 10%", ""), kind=ClaimKind.inference, profile_features=["products.loans"])
    assert "unknown_terms_as_fact" in _codes(request_, claim)


def test_same_statement_as_hypothesis_is_allowed(request_):
    claim = Claim(text="Платёж по кредиту может вырасти, если ставка плавающая; тип ставки неизвестен.",
                  kind=ClaimKind.hypothesis, profile_features=["products.loans"])
    assert "unknown_terms_as_fact" not in _codes(request_, claim)


def test_cost_of_debt_statement_without_payment_claim_is_allowed(request_):
    claim = Claim(text="Рост ставки повышает стоимость обслуживания долга.", kind=ClaimKind.inference,
                  profile_features=["products.loans"])
    assert _codes(request_, claim) == set()


@pytest.mark.parametrize("text", ["Купите облигации, пока ставка высокая.", "Инвестируйте свободные средства в валюту."])
def test_personal_investment_advice_is_an_error(request_, text):
    claim = Claim(text=text, kind=ClaimKind.recommendation, profile_features=["products.deposits"])
    assert "investment_advice" in _codes(request_, claim, section="actions")


def test_categorical_wording_is_a_warning_not_an_error(request_):
    claim = Claim(text="Нужно обязательно пересмотреть график платежей.", kind=ClaimKind.recommendation,
                  profile_features=["products.loans"])
    issues = check_claims([claim], _state(request_), section="actions")
    assert [(i.code, i.severity) for i in issues] == [("categorical_language", "warning")]
