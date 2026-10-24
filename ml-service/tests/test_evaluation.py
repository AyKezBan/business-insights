from fakes import ScriptedClient, analysis_reply, draft_reply, plan_reply
from insight_agent.evaluation import BLOCKED, FAIL, NOT_RUN, PASS, Expect, ManifestRow, run_manifest, summarize


def _row(request_, variant="base", **expect):
    return ManifestRow(test_id="T01", fixture_id="F1", case_variant=variant, label=1, request=request_, expect=Expect(**expect))


def _agent(make_agent):
    script = {"analyze_event": [analysis_reply()], "plan": [plan_reply()], "personalize": [draft_reply()]}
    return make_agent(ScriptedClient(script))


def test_passing_and_failing_rows_get_verdicts_and_metrics(make_agent, request_):
    rows = [_row(request_), _row(request_, "wrong", outcomes=["refused"])]
    report = run_manifest(_agent(make_agent), rows)
    assert [r["verdict"] for r in report] == [PASS, FAIL]
    assert report[1]["reason"] == ["outcome_allowed"]
    assert report[0]["prompt_version"] and report[0]["manual"]["depth"] is None
    summary = summarize(report)
    assert summary["M7_error_rate"]["failed"] == 0 and summary["M8_cost_rub"]["new_successful_insights"] >= 1


def test_dry_run_and_blocked_rows_do_not_call_the_model(make_agent, request_):
    agent = _agent(make_agent)
    assert run_manifest(agent, [_row(request_)], dry_run=True)[0]["verdict"] == NOT_RUN
    blocked = run_manifest(agent, [_row(request_)], blocked_reason="нет ключа")
    assert blocked[0]["verdict"] == BLOCKED and agent.client.calls == []
