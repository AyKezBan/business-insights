"""Правила приёмки ответа ML в backend-api (HttpMlClient.knownFeature, ветка feature/backend-api).

Backend отклоняет весь ответ, если модель сослалась на признак вне этого перечня, поэтому ML не должен
предлагать модели такие признаки."""
import re
from decimal import Decimal

from insight_agent.schemas import CompanyProfile, Counterparty, Products
from insight_agent.tools.profile import derive_signals

PROFILE_SIGNALS = {"okved_code", "group_revenue_rub", "revenue_rub", "foreign_trade", "group_member_okveds", "region"}
PRODUCT_FIELDS = {"settlement", "guarantees", "loans", "leasing", "deposits", "payroll", "factoring"}


def backend_knows(feature: str, profile: dict) -> bool:
    if m := re.fullmatch(r"counterparties\[(\d+)]", feature):
        return int(m.group(1)) < len(profile["counterparties"])
    if feature.startswith("products.") and feature[9:] in PRODUCT_FIELDS:
        return profile["products"].get(feature[9:]) is True
    if feature.startswith("user_provided."):
        value = profile["user_provided"].get(feature[14:])
    elif feature in PROFILE_SIGNALS:
        value = profile.get(feature)
    else:
        return False
    return value not in (None, "", [], False)


def _rich_profile() -> CompanyProfile:
    return CompanyProfile(
        company_id="U_X", group_id="G_1", okved_code="25.61", okved_name="Обработка металлов", revenue_rub=Decimal("2400000000"),
        group_revenue_rub=Decimal("9000000000"), region="Свердловская область", foreign_trade=True, foreign_trade_countries=["КИТАЙ"],
        products=Products(loans=True, leasing=True, deposits=True, factoring=True, guarantees=True, payroll=True, settlement=True),
        counterparties=[Counterparty(company_id="U_X", direction="outgoing", share_of_flow=0.8),
                        Counterparty(company_id="U_A", direction="outgoing", share_of_flow=0.1, okved_code="24.10"),
                        Counterparty(company_id="U_B", direction="incoming", share_of_flow=0.3)],
        self_transfer_share=0.8, group_member_okveds=["24.10"], user_provided={"plans": "расширение"},
    )


def test_every_feature_offered_to_the_model_is_accepted_by_the_backend():
    profile = _rich_profile()
    signals = derive_signals(profile)
    data = profile.model_dump(mode="json")
    rejected = [s.feature for s in signals if not backend_knows(s.feature, data)]
    assert rejected == []
    assert {"okved_code", "revenue_rub", "foreign_trade", "group_member_okveds", "region", "user_provided.plans"} <= {s.feature for s in signals} | {"revenue_rub"}


def test_counterparty_features_keep_the_original_index_of_the_sent_list():
    signals = {s.feature: s for s in derive_signals(_rich_profile())}
    assert set(k for k in signals if k.startswith("counterparties")) == {"counterparties[1]", "counterparties[2]"}
    assert "U_B" in signals["counterparties[2]"].value  # входящий контрагент с большей долей, но под своим исходным номером


def test_features_of_unknown_values_are_not_offered():
    bare = CompanyProfile(company_id="U_Y", products=Products(loans=False), foreign_trade=False)
    assert [s.feature for s in derive_signals(bare)] == []


def test_java_pinned_exchange_schemas_match_current_ml_protocol():
    """A stale packaged Java schema must fail even when unknown JSON fields are tolerated."""
    import json
    from pathlib import Path
    from insight_agent.schemas import Answer, Insight, InsightRequest, QuestionRequest, TraceEvent
    root = Path(__file__).resolve().parents[2]
    resources = root / "backend-api/src/main/resources/ml-contracts"
    for name, model in {"insight": Insight, "insight-request": InsightRequest, "answer": Answer,
                        "question-request": QuestionRequest, "trace-event": TraceEvent}.items():
        assert json.loads((resources / f"{name}.schema.json").read_text()) == model.model_json_schema(), name
