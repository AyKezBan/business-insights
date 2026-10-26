import threading

from fastapi.testclient import TestClient

from fakes import ScriptedClient, analysis_reply, draft_reply, plan_reply, reviewed_semantics, semantic_contents
from insight_agent.agent.orchestrator import MAX_REMEMBERED, RequestIdConflict
from insight_agent.agent.store import InsightStore
from insight_agent.agent.versions import PROMPT_VERSION, compute_version
from insight_agent.api import create_app
from insight_agent.schemas import InsightStatus, UserRole
from insight_agent.trace import load_trace
import pytest


def _script(**extra):
    script = {"analyze_event": [analysis_reply()], "plan": [plan_reply()], "personalize": [draft_reply()], "semantic_verify": [reviewed_semantics(semantic_contents(draft_reply()))]}
    script.update(extra)
    return script


def test_repeated_request_id_returns_same_insight_without_new_llm_calls(make_agent, request_):
    client = ScriptedClient(_script())
    agent = make_agent(client)
    request = request_.model_copy(update={"request_id": "req-1"})

    first = agent.run(request)
    assert client.steps().count("semantic_verify") == 1
    calls = len(client.calls)
    second = agent.run(request)

    assert len(client.calls) == calls
    assert (second.run_id, second.insight_id) == (first.run_id, first.insight_id)
    assert (first.cache_hit, second.cache_hit) == (False, True)


def test_same_request_id_with_different_content_is_a_conflict(make_agent, request_):
    agent = make_agent(ScriptedClient(_script()))
    agent.run(request_.model_copy(update={"request_id": "req-1"}))
    other = request_.model_copy(update={"request_id": "req-1", "role": UserRole.owner})
    with pytest.raises(RequestIdConflict):
        agent.run(other)


def test_parent_run_id_does_not_change_the_fingerprint(make_agent, request_):
    client = ScriptedClient(_script())
    agent = make_agent(client)
    agent.run(request_.model_copy(update={"request_id": "req-1"}))
    calls = len(client.calls)
    again = agent.run(request_.model_copy(update={"request_id": "req-1", "parent_run_id": "run_x"}))
    assert again.cache_hit and len(client.calls) == calls


def test_failed_run_is_not_remembered_so_a_retry_runs_again(make_agent, request_):
    agent = make_agent(ScriptedClient(_script()))
    request = request_.model_copy(update={"request_id": "req-1"})
    agent.settings.agent_max_tokens_per_run = 100
    assert agent.run(request).status == InsightStatus.failed

    agent.settings.agent_max_tokens_per_run = 40_000
    retry = agent.run(request)
    assert retry.status != InsightStatus.failed and not retry.cache_hit


def test_concurrent_identical_requests_pay_once(make_agent, request_):
    client = ScriptedClient(_script())
    agent = make_agent(client)
    request = request_.model_copy(update={"request_id": "req-1"})
    results = []
    threads = [threading.Thread(target=lambda: results.append(agent.run(request))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert client.steps().count("analyze_event") == 1 and client.steps().count("personalize") == 1
    assert client.steps().count("semantic_verify") == 1
    assert len({r.run_id for r in results}) == 1


def test_remembered_requests_are_bounded(make_agent, request_):
    agent = make_agent(ScriptedClient(_script()))
    for i in range(MAX_REMEMBERED + 5):
        agent._remember(agent.requests, f"r{i}", ("fp", None))
    assert len(agent.requests) == MAX_REMEMBERED and "r0" not in agent.requests


def test_insight_survives_restart_and_still_answers_questions(make_agent, request_):
    first = make_agent(ScriptedClient(_script()))
    insight = first.run(request_)

    def answer(_messages):
        return {"answerable": True, "text": "Ставка сохранится.", "claims": [
            draft_reply()["bank_view"][0]]}

    restarted = make_agent(ScriptedClient({"answer": [answer]}))
    assert restarted.records == {}
    http = TestClient(create_app(restarted))
    resp = http.post(f"/v1/insights/{insight.insight_id}/questions", json={"question": "Когда снизят ставку?"})

    assert resp.status_code == 200 and resp.json()["status"] == "answered"
    assert resp.json()["company_id"] == insight.company_id


def test_store_ignores_unknown_and_path_like_ids(tmp_path):
    store = InsightStore(tmp_path / "insights")
    assert store.load("nope") is None
    assert store.load("../../etc/passwd") is None


def test_api_returns_409_for_conflicting_request_id(make_agent, request_):
    http = TestClient(create_app(make_agent(ScriptedClient(_script()))))
    body = request_.model_copy(update={"request_id": "req-1"}).model_dump_json()
    assert http.post("/v1/insights", content=body, headers={"content-type": "application/json"}).status_code == 200
    other = request_.model_copy(update={"request_id": "req-1", "role": UserRole.owner}).model_dump_json()
    assert http.post("/v1/insights", content=other, headers={"content-type": "application/json"}).status_code == 409


def test_start_event_records_versions(make_agent, request_, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc1234")
    agent = make_agent(ScriptedClient(_script()))
    insight = agent.run(request_.model_copy(update={"request_id": "req-9"}))
    start = load_trace(agent.settings.trace_dir, insight.run_id)[0].data
    assert (start["prompt_version"], start["git_sha"], start["request_id"]) == (PROMPT_VERSION, "abc1234", "req-9")
    assert set(start["models"]) == {"light", "reasoning", "fallback"}


def test_prompt_version_changes_with_content(tmp_path):
    a, b = tmp_path / "prompts.py", tmp_path / "critic.py"
    a.write_text("one"), b.write_text("two")
    before = compute_version([a, b])
    b.write_text("three")
    assert compute_version([a, b]) != before and len(before) == 12
