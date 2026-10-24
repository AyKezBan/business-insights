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
