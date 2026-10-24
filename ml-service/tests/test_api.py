from fastapi.testclient import TestClient

from fakes import FIXTURES, ScriptedClient, analysis_reply, draft_reply, plan_reply
from insight_agent.api import create_app


def _answer_reply(messages):
    return {
        "answerable": True,
        "text": "По оценке аналитиков банка ставка сохранится до конца 2026 года.",
        "claims": [
            {"text": "Ставка останется на уровне 18%.", "kind": "assessment", "source_ids": ["bank_demo_rate_p3"]},
            {"text": "Ставка вырастет до 25%.", "kind": "fact", "source_ids": ["news_invented"]},
        ],
    }


def _client(make_agent):
    script = {
        "analyze_event": [analysis_reply()],
        "plan": [plan_reply()],
        "personalize": [draft_reply()],
        "answer": [_answer_reply],
    }
    agent = make_agent(ScriptedClient(script))
    return TestClient(create_app(agent))


def test_health(make_agent):
    body = _client(make_agent).get("/health").json()
    assert {"status": "ok", "bank_fragments": 2, "news": 1, "daily_tokens_used": 0}.items() <= body.items()
    assert body["corpus_revision"] is None


def test_insight_question_and_trace_flow(make_agent):
    http = _client(make_agent)
    resp = http.post(
        "/v1/insights",
        content=(FIXTURES / "request.json").read_text("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200
    insight = resp.json()
    assert insight["status"] == "complete"

    answer = http.post(f"/v1/insights/{insight['insight_id']}/questions", json={"question": "Когда снизят ставку?"})
    assert answer.status_code == 200
    body = answer.json()
    assert body["status"] == "answered"
    assert [c["source_ids"] for c in body["claims"]] == [["bank_demo_rate_p3"]]

    trace = http.get(f"/v1/runs/{insight['run_id']}/trace").json()
    assert trace[0]["step"] == "start" and trace[-1]["type"] == "final"


def test_unknown_insight_and_bad_run_id(make_agent):
    http = _client(make_agent)
    assert http.post("/v1/insights/nope/questions", json={"question": "?"}).status_code == 404
    assert http.get("/v1/runs/..%2F..%2Fetc/trace").status_code == 404


def test_invalid_request_is_rejected(make_agent):
    resp = _client(make_agent).post("/v1/insights", json={"role": "cfo"})
    assert resp.status_code == 422
