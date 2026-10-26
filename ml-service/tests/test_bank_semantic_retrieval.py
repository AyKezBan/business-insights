"""Synthetic reproductions of measured near-negative errors; no private excerpts."""
from datetime import date

import pytest

from insight_agent.schemas import BankFragment
from insight_agent.tools.bank import BankIndex, BankSearchArgs


def statement(identifier, text, **changes):
    values = dict(fragment_id=identifier, doc_id=identifier, doc_title="Банковская аналитика",
                  page=1, doc_date=date(2026, 9, 1), text=text,
                  review_status="verified", statement_type="reported_fact")
    values.update(changes)
    return BankFragment(**values)


@pytest.mark.parametrize("query, unrelated, supported", [
    ("Ставка налога на прибыль организаций", "Процентная ставка для организаций снижена.",
     "Ставка налога на прибыль организаций изменилась."),
    ("Refinery utilization rate", "The interest rate decreased.",
     "The refinery utilization rate increased."),
    ("Фальсификация финансовой отчётности", "Финансовые результаты организаций улучшились.",
     "Расследование выявило фальсификацию финансовой отчётности."),
    ("Спрос на золото", "Спрос населения на товары вырос.",
     "Инвестиционный спрос на золото вырос."),
])
def test_specific_query_concept_must_occur_in_evidence_not_merely_shared_generic_words(query, unrelated, supported):
    wrong = statement("wrong", unrelated, doc_title=query)
    right = statement("right", supported)
    index = BankIndex([wrong, right])
    found = index.search(BankSearchArgs(query=query, min_score=0), as_of=date(2026, 10, 26))
    assert [r.fragment.fragment_id for r in found] == ["right"]


def test_undated_gold_cannot_fall_back_to_unrelated_dated_demand_evidence():
    index = BankIndex([statement("unknown", "Спрос на золото увеличился.", doc_date=None),
                       statement("demand", "Спрос на товары увеличился.")])
    assert index.search(BankSearchArgs(query="Спрос на золото", min_score=0), as_of=date(2026, 10, 26)) == []


def test_expired_forecast_query_cannot_fall_back_to_surrounding_historical_facts():
    index = BankIndex([
        statement("fact", "Недельный рост цен и инфляция в сентябре замедлились."),
        statement("forecast", "Прогноз недельного роста цен в сентябре повышен.",
                  statement_type="forecast", target_period="1–7 сентября 2026", valid_until=date(2026, 9, 7)),
    ])
    args = BankSearchArgs(query="Прогноз недельного роста цен в сентябре", min_score=0)
    assert [r.fragment.fragment_id for r in index.search(args, as_of=date(2026, 9, 3))] == ["forecast"]
    assert index.search(args, as_of=date(2026, 10, 26)) == []


def test_rating_outlook_is_a_reported_rating_action_not_a_macroeconomic_forecast():
    index = BankIndex([statement("rating", "АКРА подтвердило кредитный рейтинг РусГидро AAA, прогноз стабильный."),
                       statement("forecast", "Прогноз инфляции повышен.", statement_type="forecast", valid_until=date(2026, 12, 31))])
    found = index.search(BankSearchArgs(query="Кредитный рейтинг и прогноз АКРА по РусГидро", min_score=0), as_of=date(2026, 10, 26))
    assert [r.fragment.fragment_id for r in found] == ["rating"]


def test_future_exact_security_cannot_be_replaced_with_another_bond_issue():
    index = BankIndex([statement("future", "Доходность ОФЗ 26254 выросла.", doc_date=date(2026, 9, 14)),
                       statement("other", "Доходность ОФЗ 26218 выросла.")])
    assert index.search(BankSearchArgs(query="Доходность ОФЗ 26254", min_score=0), as_of=date(2026, 9, 12)) == []


def test_weekly_forecast_must_match_requested_day_range_not_an_annual_forecast():
    index = BankIndex([
        statement("old_week", "С 1 по 7 сентября ожидается рост цен.", statement_type="forecast", valid_until=date(2026, 9, 7)),
        statement("annual", "Прогноз роста цен на конец 2026 года повышен.", statement_type="forecast", valid_until=date(2026, 12, 31)),
    ])
    assert index.search(BankSearchArgs(query="Какой рост цен прогнозируется на неделю 8–14 сентября 2026 года?", min_score=0), as_of=date(2026, 9, 8)) == []


def test_spelled_out_half_year_period_matches_source_abbreviation():
    index = BankIndex([statement("half", "За 1П26 приток физлиц в облигации вырос относительно 2П25."),
                       statement("year", "За 2026 год приток средств инвесторов в облигации вырос.")])
    found = index.search(BankSearchArgs(query="Приток частных инвесторов в облигации за первое полугодие 2026 года относительно второго полугодия 2025 года", min_score=0), as_of=date(2026, 10, 26))
    assert found[0].fragment.fragment_id == "half"


@pytest.mark.parametrize("query,text", [
    ("Частные инвесторы", "Частные инвесторы увеличили вложения."),
    ("Первое полугодие 2026 года", "Первое полугодие 2026 года показало рост."),
])
def test_aliases_preserve_matches_when_source_also_spells_out_the_terms(query, text):
    index = BankIndex([statement("natural", text)])
    assert [r.fragment.fragment_id for r in index.search(BankSearchArgs(query=query, min_score=0), as_of=date(2026, 10, 26))] == ["natural"]


def test_weekly_rating_outlook_remains_a_reported_fact():
    index = BankIndex([statement("rating", "На этой неделе АКРА подтвердило кредитный рейтинг РусГидро AAA, прогноз стабильный.")])
    assert [r.fragment.fragment_id for r in index.search(BankSearchArgs(query="Какой кредитный рейтинг и прогноз АКРА по РусГидро на этой неделе?", min_score=0), as_of=date(2026, 10, 26))] == ["rating"]


def test_zero_padded_day_range_matches_same_calendar_interval():
    index = BankIndex([statement("forecast", "На неделе с 1 по 7 сентября ожидается рост цен.",
        statement_type="forecast", target_period="1–7 сентября 2026", valid_until=date(2026, 9, 7))])
    assert [r.fragment.fragment_id for r in index.search(BankSearchArgs(query="Прогноз роста цен на 01–07 сентября 2026", min_score=0), as_of=date(2026, 9, 3))] == ["forecast"]


def test_creditworthiness_request_cannot_use_general_forecasts_before_rating_publication():
    index = BankIndex([statement("rating", "АКРА подтвердило кредитоспособность РусГидро.", doc_date=date(2026, 9, 14)),
                       statement("other", "Оценка после пересмотра стала выше, прогноз обновлен.")])
    args = BankSearchArgs(query="Оценка кредитоспособности РусГидро агентством АКРА после пересмотра", min_score=0)
    assert index.search(args, as_of=date(2026, 9, 10)) == []
    assert [r.fragment.fragment_id for r in index.search(args, as_of=date(2026, 9, 15))] == ["rating"]


def test_open_weekly_forecast_request_does_not_substitute_annual_projection():
    index = BankIndex([statement("annual", "Прогноз роста цен на конец 2026 года повышен.",
                                statement_type="forecast", target_period="Конец 2026", valid_until=date(2026, 12, 31))])
    assert index.search(BankSearchArgs(query="Есть ли действующий прогноз недельного роста цен?", min_score=0), as_of=date(2026, 9, 10)) == []


@pytest.mark.parametrize("query,text", [
    ("Инвесторы", "Частные инвесторы увеличили вложения."),
    ("Полугодие", "Первое полугодие 2026 года показало рост."),
])
def test_index_aliases_keep_original_words_searchable(query, text):
    index = BankIndex([statement("natural", text)])
    assert index.search(BankSearchArgs(query=query, min_score=0), as_of=date(2026, 10, 26))


def test_weekly_creditworthiness_outlook_uses_same_rating_concept_exception():
    index = BankIndex([statement("rating", "На этой неделе АКРА подтвердило кредитоспособность РусГидро, прогноз стабильный.")])
    assert index.search(BankSearchArgs(query="Какой прогноз кредитоспособности РусГидро агентства АКРА на этой неделе?", min_score=0), as_of=date(2026, 10, 26))


def test_month_long_forecast_interval_is_not_weekly_evidence():
    index = BankIndex([statement("month", "Прогноз роста цен на 1–30 сентября 2026 повышен.",
                                statement_type="forecast", target_period="1–30 сентября 2026", valid_until=date(2026, 9, 30))])
    assert index.search(BankSearchArgs(query="Есть ли действующий прогноз недельного роста цен?", min_score=0), as_of=date(2026, 9, 3)) == []
