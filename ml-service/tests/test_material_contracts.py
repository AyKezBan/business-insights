"""Protect real source provenance and the meaning of imported company values."""
import pytest
from pydantic import ValidationError

from insight_agent.schemas import BankFragment, CompanyProfile, SourceRef
from insight_agent.tools.profile import derive_signals, missing_profile_data


def test_source_roundtrip_keeps_document_and_fragment_hash():
    ref = SourceRef.model_validate(dict(source_id="bank:p1", kind="bank", title="Report",
        doc_id="bank-123", page=1, url="/v1/materials/documents/bank-123/file#page=1",
        content_sha256="a" * 64, excerpt="Confirmed paragraph"))
    assert ref.model_dump()["doc_id"] == "bank-123"
    assert ref.model_dump()["content_sha256"] == "a" * 64
    assert SourceRef.model_validate_json(ref.model_dump_json()).excerpt == "Confirmed paragraph"


@pytest.mark.parametrize("override", [{"page": 0}, {"content_sha256": "not-a-hash"}])
def test_source_rejects_invalid_provenance(override):
    with pytest.raises(ValidationError):
        SourceRef(source_id="x", kind="bank", title="Report", **override)


def test_legacy_fragment_is_unreviewed():
    frag = BankFragment(fragment_id="f", doc_id="d", doc_title="Report", text="Text")
    assert frag.review_status == "pending"


@pytest.mark.parametrize("revenue", [0, 999])
def test_observed_small_revenue_is_not_missing(revenue):
    p = CompanyProfile(company_id="demo", revenue_rub=revenue, revenue_status="known",
                       revenue_year=2025, revenue_scope="unknown")
    assert "revenue_rub" in {s.feature for s in derive_signals(p)}
    assert "revenue_rub" not in missing_profile_data(p)
    signal = next(s for s in derive_signals(p) if s.feature == "revenue_rub")
    assert "2025" in signal.note


def test_payment_counterparty_does_not_imply_supplier_relationship():
    p = CompanyProfile(company_id="demo", counterparties=[{"company_id":"other", "direction":"outgoing"}])
    value = next(s.value for s in derive_signals(p) if s.feature.startswith("counterparties"))
    assert "исходящий" in value
    assert "поставщик" not in value


def test_fts_absence_is_unknown_instead_of_no_trade():
    p = CompanyProfile(company_id="demo", foreign_trade=False, foreign_trade_data_available=False)
    assert p.foreign_trade is None


def test_legacy_placeholder_is_normalized():
    p = CompanyProfile(company_id="demo", revenue_rub=1000)
    assert p.revenue_status == "placeholder"
    assert p.revenue_rub is None
