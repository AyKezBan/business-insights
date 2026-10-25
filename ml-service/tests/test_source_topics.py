"""Tax and energy terminology must not acquire a borrowing-cost meaning."""
import pytest

from insight_agent.materials.news import classify_topics


@pytest.mark.parametrize("text", [
    "Налоговая ставка составляет 2%. Срок уплаты налога — 28 октября.",
    "U.S. refinery utilization rates averaged 95% of production capacity.",
    "Electricity generation rates increased.",
])
def test_non_interest_rates_do_not_imply_borrowing_cost(text):
    assert "cost_of_debt" not in classify_topics(text)


@pytest.mark.parametrize("text, expected", [
    ("Банк России снизил ключевую ставку до 14%.", "cost_of_debt"),
    ("The central bank lowered interest rates.", "cost_of_debt"),
    ("Налоговая отчетность: срок уплаты налога 28 октября.", "tax"),
    ("Налоговая отчетность: срок уплаты налога 28 октября.", "reporting"),
    ("Налоговая отчетность: срок уплаты налога 28 октября.", "deadlines"),
    ("U.S. refinery utilization reached 95% of capacity.", "production_capacity"),
    ("U.S. refinery utilization reached 95% of capacity.", "energy"),
    ("Fuel and electricity prices increased.", "input_prices"),
])
def test_source_subjects_have_explicit_topic_evidence(text, expected):
    assert expected in classify_topics(text)


@pytest.mark.parametrize("text, expected_topic", [
    pytest.param(
        "Key-rate increases raise borrowing costs.", "cost_of_debt",
        id="hyphenated-key-rate-and-borrowing-costs",
    ),
    pytest.param(
        "Банк повысил процентную ставку по кредиту.", "cost_of_debt",
        id="russian-interest-rate-and-credit",
    ),
    pytest.param("Валютный курс рубля изменился.", "fx", id="legacy-fx"),
    pytest.param(
        "Потребительский спрос и розничные продажи выросли.", "domestic_demand",
        id="legacy-domestic-demand",
    ),
    pytest.param("Зарплаты и стоимость труда выросли.", "labor_costs", id="legacy-labor-costs"),
    pytest.param(
        "Изменился норматив регулирования тарифов.", "regulation",
        id="legacy-regulation",
    ),
    pytest.param(
        "Рейтинг контрагента снижен из-за банкротства.", "counterparty_risk",
        id="legacy-counterparty-risk",
    ),
    pytest.param("Нефть, золото и сталь подорожали.", "commodity_prices", id="legacy-commodity-prices"),
])
def test_financial_phrases_and_legacy_channels_keep_their_topics(text, expected_topic):
    assert expected_topic in classify_topics(text)


@pytest.mark.parametrize("text, forbidden_topic", [
    pytest.param("Rate remains 17%.", "cost_of_debt", id="bare-english-rate"),
    pytest.param("Ставка составляет 2%.", "cost_of_debt", id="bare-russian-rate"),
    pytest.param(
        "Tax credits support renewable energy.", "cost_of_debt",
        id="tax-credit-is-not-borrowing-cost",
    ),
    pytest.param(
        "Private operating costs increased.", "tax",
        id="private-substring-is-not-vat",
    ),
])
def test_ambiguous_words_and_substrings_do_not_create_financial_topics(text, forbidden_topic):
    assert forbidden_topic not in classify_topics(text)
