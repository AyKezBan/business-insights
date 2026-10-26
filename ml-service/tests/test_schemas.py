import json

from insight_agent.export_schemas import CONTRACTS, main as export_schemas
from insight_agent.schemas import CompanyProfile, InsightRequest


def test_fixture_request_is_valid(request_):
    assert request_.profile.products.loans is True
    assert request_.role.value == "cfo"


def test_unknown_product_stays_none_not_false():
    profile = CompanyProfile(company_id="U_X")
    assert profile.products.loans is None
    assert profile.foreign_trade is None


def test_export_writes_every_contract(tmp_path):
    export_schemas(str(tmp_path))
    for name in CONTRACTS:
        schema = json.loads((tmp_path / f"{name}.schema.json").read_text("utf-8"))
        assert schema["type"] == "object"


def test_request_schema_requires_profile_and_event():
    schema = InsightRequest.model_json_schema()
    assert set(schema["required"]) == {"profile", "event"}


def test_contract_money_fields_accept_number_or_decimal_string():
    props = InsightRequest.model_json_schema()["$defs"]["CompanyProfile"]["properties"]
    for field in ("revenue_rub", "group_revenue_rub"):
        kinds = {branch.get("type") for branch in props[field]["anyOf"]}
        assert {"number", "string"} <= kinds


def test_profile_accepts_revenue_status_vocabulary_of_data_import():
    from decimal import Decimal

    observed = CompanyProfile.model_validate(
        {"company_id": "U_1", "revenue_rub": "2400000000", "revenue_status": "observed", "cl_type": "CLOSE_CLIENT"}
    )
    assert observed.revenue_status == "known" and observed.revenue_rub == Decimal("2400000000")
    for status in ("placeholder", "missing", "invalid"):
        assert CompanyProfile.model_validate({"company_id": "U_1", "revenue_status": status}).revenue_rub is None
