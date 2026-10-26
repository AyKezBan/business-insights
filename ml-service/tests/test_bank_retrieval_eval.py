import importlib.util
from datetime import date
from pathlib import Path

import pytest

from insight_agent.schemas import BankFragment
from insight_agent.tools.bank import BankIndex

spec = importlib.util.spec_from_file_location("bank_eval", Path(__file__).resolve().parents[1] / "eval/run_bank_retrieval.py")
bank_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bank_eval)


def test_release_gate_requires_all_old_gold_and_zero_negative_false_positives():
    assert hasattr(bank_eval, "passes_release"), "Old runner accepts 8/10 and does not evaluate near-negatives"
    assert bank_eval.passes_release(10, 10, 12, 6, 6)
    assert not bank_eval.passes_release(9, 12, 12, 6, 6)
    assert not bank_eval.passes_release(10, 9, 12, 6, 6)
    assert not bank_eval.passes_release(10, 12, 12, 5, 6)
    assert not bank_eval.passes_release(10, 0, 0, 0, 0)


def test_eval_uses_each_cases_time_window_and_marks_empty_expected_as_refusal():
    assert hasattr(bank_eval, "evaluate_cases"), "Cases lack negative/time-window support"
    index = BankIndex([BankFragment(fragment_id="f", doc_id="d", page=1,
        doc_title="Золото", text="Спрос на золото вырос.", doc_date=date(2026, 9, 1),
        review_status="verified", statement_type="reported_fact")])
    results = bank_eval.evaluate_cases(index, [
        {"query": "золото", "as_of": "2026-10-26", "expected_fragment_ids": ["f"], "search_args": {"min_score": 0}},
        {"query": "золото", "as_of": "2026-08-01", "expected_fragment_ids": [], "search_args": {"min_score": 0}},
        {"query": "золото", "as_of": "2026-10-26", "expected_fragment_ids": [], "search_args": {"min_score": 0}},
        {"query": "золото", "as_of": "2026-10-26", "expected_fragment_ids": [],
         "search_args": {"not_after": "2026-08-01", "min_score": 0}},
    ])
    assert [r["passed"] for r in results] == [True, True, False, True]
    assert results[2]["top3"] == ["f"]


def test_release_suite_rejects_negative_rows_disguised_as_positive_gold():
    assert hasattr(bank_eval, "evaluate_suites"), "Suite shape is not validated"
    empty = {"query": "золото", "as_of": "2026-10-26", "expected_fragment_ids": []}
    with pytest.raises(ValueError, match="positive"):
        bank_eval.evaluate_suites(BankIndex([]), [empty] * 10, [empty] * 12, [empty] * 6)
