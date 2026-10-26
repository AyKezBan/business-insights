"""Совместимость с контрактом интерфейса d09-d10 (версия 2.0, ветка feature/d09-d10-clarity)."""
import pytest
from fastapi.testclient import TestClient

from fakes import ScriptedClient, analysis_reply, draft_reply, plan_reply, reviewed_semantics, semantic_contents
from insight_agent.api import create_app
from insight_agent.frontend_adapter import BANK_ABSENT, to_frontend_card
from insight_agent.tools.bank import BankIndex

# Метки, которые знает интерфейс (certaintyLabels в web/app.js).
UI_CERTAINTY = {"fact", "inference", "hypothesis", "assessment", "recommendation", "unavailable"}


def _insight(make_agent, request_, **draft):
    client = ScriptedClient({"analyze_event": [analysis_reply()], "plan": [plan_reply()], "personalize": [draft_reply(**draft)], "semantic_verify": [reviewed_semantics(semantic_contents(draft_reply(**draft)))]})
    bank = BankIndex([]) if draft.get("with_bank") is False else None
    return make_agent(client, bank=bank).run(request_)


def _blocks(card):
    return {b["kind"]: b for b in card["blocks"]}


def test_overview_has_fact_bank_and_ai_blocks_with_ui_fields(make_agent, request_):
    card = to_frontend_card(_insight(make_agent, request_))
    blocks = _blocks(card)
    assert {"fact", "bank", "ai"} <= set(blocks)
    assert blocks["fact"]["certainty"] == "fact" and "18%" in blocks["fact"]["text"]
    assert [s["kind"] for s in blocks["ai"]["sections"]] == ["consequences", "risks", "recommendations"]
    assert {"company_id", "role", "event_id", "view"} == set(card["context"])
    assert card["disclaimer"] and card["status"] in ("complete", "limited", "failed")


def test_every_certainty_is_known_and_every_source_id_is_listed(make_agent, request_):
    card = to_frontend_card(_insight(make_agent, request_))
    listed = {s["id"] for s in card["sources"]}
    for block in card["blocks"]:
        assert block["source_ids"] and set(block["source_ids"]) <= listed or block["kind"] == "bank"
        for section in block.get("sections", [block]):
            assert section["certainty"] in UI_CERTAINTY and set(section["source_ids"]) <= listed
    assert all({"id", "title", "date", "page"} <= set(s) for s in card["sources"])


def test_bank_assessment_goes_to_bank_block_and_never_to_ai(make_agent, request_):
    blocks = _blocks(to_frontend_card(_insight(make_agent, request_)))
    consequences = next(s for s in blocks["bank"]["sections"] if s["kind"] == "consequences")
    assert consequences["certainty"] == "assessment" and "bank_demo_rate_p3" in consequences["source_ids"]
    assert all(s["certainty"] != "assessment" for s in blocks["ai"]["sections"])
    others = [s for s in blocks["bank"]["sections"] if s["kind"] != "consequences"]
    assert others and all(s["certainty"] == "unavailable" and s["source_ids"] == [] for s in others)


def test_missing_bank_material_is_stated_in_every_bank_section(make_agent, request_):
    card = to_frontend_card(_insight(make_agent, request_, with_bank=False))
    bank = _blocks(card)["bank"]
    assert bank["source_ids"] == []
    assert all(s["certainty"] == "unavailable" and s["text"] == BANK_ABSENT for s in bank["sections"])


@pytest.mark.parametrize("view", ["consequences", "risks", "recommendations"])
def test_theme_views_drop_the_fact_block_and_keep_one_theme(make_agent, request_, view):
    card = to_frontend_card(_insight(make_agent, request_), view)
    assert "fact" not in _blocks(card) and card["context"]["view"] == view
    assert all(s["kind"] == view for b in card["blocks"] for s in b["sections"])


def test_unknown_view_is_rejected(make_agent, request_):
    with pytest.raises(ValueError):
        to_frontend_card(_insight(make_agent, request_), "all")


def _http(make_agent, request_):
    answer = lambda _m: {"answerable": True, "text": "Ставка сохранится.", "claims": [  # noqa: E731
        draft_reply()["bank_view"][0]]}
    client = ScriptedClient({"analyze_event": [analysis_reply()], "plan": [plan_reply()], "personalize": [draft_reply()], "semantic_verify": [reviewed_semantics(semantic_contents(draft_reply()))], "answer": [answer]})
    http = TestClient(create_app(make_agent(client)))
    insight = http.post("/v1/insights", content=request_.model_dump_json(), headers={"content-type": "application/json"}).json()
    return http, insight["insight_id"]


def test_card_endpoint_serves_the_ui_shape(make_agent, request_):
    http, insight_id = _http(make_agent, request_)
    ok = http.get(f"/v1/insights/{insight_id}/card", params={"view": "risks"})
    assert ok.status_code == 200 and ok.json()["insight_id"] == insight_id
    assert http.get(f"/v1/insights/{insight_id}/card", params={"view": "bad"}).status_code == 422
    assert http.get("/v1/insights/nope/card").status_code == 404


def test_clarification_endpoint_echoes_request_id_and_uses_ui_fields(make_agent, request_):
    http, insight_id = _http(make_agent, request_)
    resp = http.post(f"/v1/insights/{insight_id}/clarification",
                     json={"question": "Когда снизят ставку?", "request_id": "frontend-uuid", "view": "risks"})
    body = resp.json()
    assert resp.status_code == 200
    assert {"context", "insight_id", "request_id", "answer_id", "status", "text", "sources", "missing_data"} <= set(body)
    assert (body["request_id"], body["insight_id"], body["context"]["view"]) == ("frontend-uuid", insight_id, "risks")
    assert body["status"] in ("answered", "no_answer")
    assert http.post("/v1/insights/nope/clarification", json={"question": "?"}).status_code == 404
