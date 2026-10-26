"""The full-stack fixture must exercise the production semantic gate with a finite review."""
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


def test_scripted_stack_publishes_reviewed_context_without_external_model_calls(tmp_path, request_, monkeypatch):
    monkeypatch.setenv("TRACE_DIR", str(tmp_path / "traces"))
    path = Path(__file__).resolve().parents[2] / "infra/smoke/smoke_app.py"
    spec = importlib.util.spec_from_file_location("synthetic_stack_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    with TestClient(fixture.create_smoke_app()) as http:
        payload = request_.model_dump(mode="json") | {"request_id": "smoke-regression"}
        response = http.post("/v1/insights", json=payload)
        assert response.status_code == 200
        insight = response.json()
        assert insight["outcome"] in {"full", "limited"}
        assert insight["request_id"] == "smoke-regression"
        assert insight["self_check"]["passed"]
        assert insight["why_it_matters"] and insight["bank_view"]
        response = http.post(f"/v1/insights/{insight['insight_id']}/questions",
                             json={"question": "Какая ставка указана в синтетической новости?"})
        assert response.status_code == 200
        answer = response.json()
        assert answer["status"] == "answered"
        assert answer["claims"] and answer["claims"][0]["evidence_spans"]
        assert answer["as_of"] == insight["as_of"]
        health = http.get("/smoke/health").json()
        assert health["external_llm_calls"] == 0
        assert health["semantic_reviews"] >= 1
