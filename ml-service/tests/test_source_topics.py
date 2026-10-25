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
