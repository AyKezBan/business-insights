"""Offline release runner contracts: real snapshots/agent, fake provider boundary."""
from datetime import UTC, datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest

from fakes import ScriptedClient
from insight_agent.llm import LLMError, OpenAICompatibleClient
from insight_agent.materials.identity import event_content_sha256
from insight_agent.materials.models import CorpusSnapshot, SourceVersion
from insight_agent.materials.store import load_catalog_token, publish_snapshot
from insight_agent.schemas import NewsEvent, SourceRef


RUNNER = Path(__file__).parents[1] / "eval" / "run_release_acceptance.py"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("Acceptance runner tests must never use a real network transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)


def runner():
    assert RUNNER.is_file(), "Explicit release acceptance runner is missing"
    spec = importlib.util.spec_from_file_location("release_acceptance_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "store"
    observed = datetime(2026, 10, 25, tzinfo=UTC)
    events, versions = [], []
    for i in range(3):
        text = f"Synthetic bulletin {i}; no private article."
        digest = hashlib.sha256(text.encode()).hexdigest()
        url = f"https://example.test/news/{i}"
        events.append(NewsEvent(event_id=f"event-{i}", title=f"Bulletin {i}", text=text,
                                published_at=observed,
                                source=SourceRef(source_id=f"source-{i}", kind="news", title=f"Bulletin {i}",
                                                 url=url, content_sha256=digest)))
        versions.append(SourceVersion(source_id=f"source-{i}", event_id=f"event-{i}", url=url,
                                      revision=1, content_sha256=digest, text=text,
                                      first_seen_at=observed, retrieved_at=observed, published_at=observed))
    revision = publish_snapshot(root, CorpusSnapshot(news=events, source_versions=versions))
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({"profiles": [{"company_id": f"company-{i}", "products": {"loans": i == 0}}
                                                for i in range(3)]}))
    manifest = {
        "frozen_at": observed.isoformat(), "corpus_revision": revision,
        "catalog_token": load_catalog_token(root).model_dump(mode="json"),
        "profiles_file_sha256": hashlib.sha256(profiles.read_bytes()).hexdigest(),
        "events": [{"event_id": e.event_id, "event_content_sha256": event_content_sha256(e),
                    "source_id": e.source.source_id, "content_sha256": e.source.content_sha256,
                    "url": e.source.url} for e in events],
        "pairs": [{"case_id": f"case-{e}-{p}", "event_id": f"event-{e}", "company_id": f"company-{p}",
                   "role": "owner" if p == 2 else "cfo", "question": f"Synthetic question {e}-{p}?"}
                  for e in range(3) for p in range(3)],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return {"root": root, "profiles": profiles, "manifest": manifest_path, "data": manifest,
            "out": tmp_path / "private-output"}


def args(inputs, *extra):
    return ["--manifest", str(inputs["manifest"]), "--store", str(inputs["root"]),
            "--profiles", str(inputs["profiles"]), "--out", str(inputs["out"]), *extra]


def forbidden_client(_):
    pytest.fail("Client must not be constructed for dry-run or invalid/finished inputs")


def fake_client(_):
    unavailable = [{"text": "Insufficient synthetic evidence.", "kind": "unavailable"}]
    return ScriptedClient({
        "analyze_event": [{"event_id": "synthetic", "summary": "Synthetic", "facts": [], "actors": [],
                           "impact_channels": [], "bank_queries": [], "open_questions": []}],
        "plan": [{"steps": []}],
        "personalize": [{"headline": "Synthetic result", "why_it_matters": unavailable,
                         "consequences": unavailable, "risks": [], "opportunities": [], "actions": unavailable,
                         "bank_view": unavailable, "impact_level": "low", "impact_rationale": "Unknown"}],
        "answer": [{"answerable": False, "text": "Unknown", "claims": [], "missing_data": ["Synthetic evidence"]}],
    })


def live_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-secret-never-print")
    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.example.test/v1")


def test_default_is_offline_and_does_not_read_env_file_or_create_client(inputs, capsys):
    module = runner()
    assert module.main(args(inputs, "--env-file", "/nonexistent/secret.env"), client_factory=forbidden_client) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "dry-run" and summary["selected_cases"] == 9 and summary["provider_calls"] == 0
    assert not inputs["out"].exists()


@pytest.mark.parametrize("mutation", ["profile-file", "event-hash", "source-url", "catalog-token", "unknown-company", "duplicate-pair"])
def test_invalid_inputs_fail_before_client_construction(inputs, mutation, capsys):
    data = inputs["data"]
    if mutation == "profile-file":
        inputs["profiles"].write_text('{"profiles": []}')
    elif mutation == "event-hash":
        data["events"][0]["event_content_sha256"] = "0" * 64
    elif mutation == "source-url":
        data["events"][0]["url"] = "https://example.test/wrong"
    elif mutation == "catalog-token":
        data["catalog_token"]["generation"] += 1
    elif mutation == "unknown-company":
        data["pairs"][0]["company_id"] = "missing"
    else:
        data["pairs"].append(data["pairs"][0])
    inputs["manifest"].write_text(json.dumps(data))
    assert runner().main(args(inputs, "--live"), client_factory=forbidden_client) == 2
    assert not inputs["out"].exists()
    assert "Synthetic bulletin" not in capsys.readouterr().out


def test_nine_pairs_and_qa_keep_context_versions_and_manual_verdict(inputs, monkeypatch, capsys):
    live_env(monkeypatch)
    assert runner().main(args(inputs, "--live"), client_factory=fake_client) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["completed_cases"] == 9 and summary["mode"] == "live"
    results = [json.loads(p.read_text()) for p in sorted(inputs["out"].glob("cases/*/result.json"))]
    assert len(results) == 9
    for result, pair in zip(results, inputs["data"]["pairs"]):
        assert result["case_id"] == pair["case_id"]
        assert result["manual_verdict"] == "NOT_REVIEWED"
        assert result["request"]["role"] == pair["role"]
        assert result["request"]["profile"]["company_id"] == pair["company_id"]
        assert result["request"]["event"]["event_id"] == pair["event_id"]
        assert result["insight"]["corpus_revision"] == inputs["data"]["corpus_revision"]
        assert result["qa"]["corpus_revision"] == result["insight"]["corpus_revision"]
        assert result["qa"]["event_content_sha256"] == result["insight"]["event_content_sha256"]
        assert result["qa_question"] == pair["question"]
        assert len(result["identity"]["profile_sha256"]) == 64
        assert result["versions"]["prompt"] and result["versions"]["code"] != "unknown"
        assert len(result["versions"]["schema_sha256"]) == 64
        assert result["latency_ms"] >= 0
        assert result["attempts"] and result["traces"]["insight"] and result["traces"]["qa"]
    serialized = json.dumps(results)
    assert "synthetic-secret-never-print" not in serialized
    assert "Synthetic bulletin" not in capsys.readouterr().out
    assert inputs["out"].stat().st_mode & 0o077 == 0


def test_completed_cases_are_skipped_without_client_or_key_and_incomplete_refuses(inputs, monkeypatch, capsys):
    live_env(monkeypatch)
    module = runner()
    subset = args(inputs, "--live", "--case-id", "case-0-0")
    assert module.main(subset, client_factory=fake_client) == 0
    result = inputs["out"] / "cases/case-0-0/result.json"
    before = result.read_bytes()
    monkeypatch.delenv("LLM_API_KEY")
    assert module.main(subset, client_factory=forbidden_client) == 0
    assert result.read_bytes() == before
    result.unlink()
    assert module.main(subset, client_factory=forbidden_client) == 2
    assert "incomplete" in capsys.readouterr().out.lower()


def test_existing_result_from_different_request_is_refused(inputs, monkeypatch):
    live_env(monkeypatch)
    module = runner()
    subset = args(inputs, "--live", "--case-id", "case-0-0")
    assert module.main(subset, client_factory=fake_client) == 0
    inputs["data"]["pairs"][0]["question"] = "Changed question?"
    inputs["manifest"].write_text(json.dumps(inputs["data"]))
    assert module.main(subset, client_factory=forbidden_client) == 2


def test_gateway_raw_cost_and_local_ruble_estimate_are_distinct(inputs, monkeypatch, capsys):
    live_env(monkeypatch)
    fake = fake_client(None)

    def respond(request):
        body = json.loads(request.content)
        response = fake.chat(body["model"], body["messages"], json_mode=True)
        return httpx.Response(200, json={"choices": [{"message": {"content": response.text}}],
                                         "model": response.model,
                                         "usage": {"prompt_tokens": 800, "completion_tokens": 400,
                                                   "cost": "0.000123", "currency": "USD"}})

    def factory(settings):
        client = OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key, settings.llm_timeout_seconds)
        client._http.close()
        client._http = httpx.Client(base_url=settings.llm_base_url, transport=httpx.MockTransport(respond))
        return client

    assert runner().main(args(inputs, "--live", "--case-id", "case-0-0"), client_factory=factory) == 0
    result = json.loads((inputs["out"] / "cases/case-0-0/result.json").read_text())
    costs = result["accounting"]["gateway_usage"]
    assert costs and all(x["usage"]["cost"] == "0.000123" and x["currency"] == "USD" for x in costs)
    assert result["accounting"]["local_estimate"]["currency"] == "RUB"
    assert result["accounting"]["local_estimate"]["is_estimate"] is True
    assert "synthetic-secret-never-print" not in capsys.readouterr().out


def test_unknown_gateway_currency_is_not_assumed_to_be_rubles(inputs, monkeypatch):
    live_env(monkeypatch)
    module = runner()
    client = fake_client(None)
    journal = module.JournalClient(client, inputs["out"], secret="synthetic-secret-never-print")
    journal.observe_response(httpx.Response(200, json={"usage": {"cost": 0.2}},
                                             request=httpx.Request("POST", "https://example.test/chat/completions")))
    assert journal.gateway_usage[0]["currency"] == "unknown"


def test_disallowed_model_and_invalid_budgets_do_not_create_client(inputs):
    module = runner()
    assert module.main(args(inputs, "--model", "unapproved-model"), client_factory=forbidden_client) == 2
    assert module.main(args(inputs, "--run-token-budget", "0"), client_factory=forbidden_client) == 2


def test_provider_error_cannot_echo_key_into_traces_or_stdout(inputs, monkeypatch, capsys):
    live_env(monkeypatch)

    def factory(_):
        return ScriptedClient({"analyze_event": [LLMError("echo synthetic-secret-never-print")]})

    assert runner().main(args(inputs, "--live", "--case-id", "case-0-0"), client_factory=factory) == 1
    for path in inputs["out"].rglob("*"):
        if path.is_file():
            assert b"synthetic-secret-never-print" not in path.read_bytes(), str(path)
    assert "synthetic-secret-never-print" not in capsys.readouterr().out
    result = json.loads((inputs["out"] / "cases/case-0-0/result.json").read_text())
    assert result["errors"] and result["attempts"][0]["error"]["type"] == "LLMError"
    assert result["manual_verdict"] == "NOT_REVIEWED"


def test_transport_json_mode_fallback_counts_both_requests(inputs, monkeypatch, capsys):
    live_env(monkeypatch)
    fake = fake_client(None)

    def respond(request):
        body = json.loads(request.content)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "JSON mode unsupported"})
        response = fake.chat(body["model"], body["messages"], json_mode=True)
        return httpx.Response(200, json={"choices": [{"message": {"content": response.text}}],
                                         "usage": {"prompt_tokens": 800, "completion_tokens": 400, "cost": 0.2}})

    def factory(settings):
        client = OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key)
        client._http.close()
        client._http = httpx.Client(base_url=settings.llm_base_url, transport=httpx.MockTransport(respond))
        return client

    assert runner().main(args(inputs, "--live", "--case-id", "case-0-0"), client_factory=factory) == 0
    result = json.loads((inputs["out"] / "cases/case-0-0/result.json").read_text())
    assert result["provider_requests"] == 2 * len(result["attempts"])
    assert json.loads(capsys.readouterr().out)["provider_calls"] == result["provider_requests"]
    raw = (inputs["out"] / "cases/case-0-0/gateway-requests.jsonl").read_text().splitlines()
    assert len(raw) == result["provider_requests"]


def test_explicit_case_env_file_and_bounded_subset(inputs, monkeypatch, tmp_path):
    for name in ("LLM_API_KEY", "API_KEY", "LLM_BASE_URL", "BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / "synthetic.env"
    env_file.write_text("API_KEY=synthetic-only\nBASE_URL=https://gateway.example.test/v1\n")
    assert runner().main(args(inputs, "--live", "--env-file", str(env_file), "--case-id", "case-1-2"),
                         client_factory=fake_client) == 0
    assert len(list(inputs["out"].glob("cases/*/result.json"))) == 1


def test_changed_current_pointer_does_not_change_frozen_request(inputs, monkeypatch):
    # A new current publication must not substitute its event for the pinned revision.
    old_revision = inputs["data"]["corpus_revision"]
    publish_snapshot(inputs["root"], CorpusSnapshot())
    live_env(monkeypatch)
    assert runner().main(args(inputs, "--live", "--case-id", "case-0-0"), client_factory=fake_client) == 0
    result = json.loads((inputs["out"] / "cases/case-0-0/result.json").read_text())
    assert result["insight"]["corpus_revision"] == old_revision
    assert result["qa"]["corpus_revision"] == old_revision


def test_model_dump_preserves_new_evidence_fields():
    from pydantic import BaseModel

    class FutureClaim(BaseModel):
        text: str
        evidence: list[dict]

    value = runner()._dump(FutureClaim(text="Synthetic claim", evidence=[{"source_id": "s", "quote": "q"}]))
    assert value["evidence"] == [{"source_id": "s", "quote": "q"}]


def test_interrupted_case_cannot_be_replayed(inputs, monkeypatch):
    live_env(monkeypatch)

    class InterruptedClient:
        def chat(self, model, messages, *, json_mode):
            raise KeyboardInterrupt("Synthetic interruption")

    module = runner()
    selected = args(inputs, "--live", "--case-id", "case-0-0")
    with pytest.raises(KeyboardInterrupt):
        module.main(selected, client_factory=lambda _: InterruptedClient())
    assert (inputs["out"] / "cases/case-0-0/started.json").exists()
    assert not (inputs["out"] / "cases/case-0-0/result.json").exists()
    assert module.main(selected, client_factory=forbidden_client) == 2


def test_structured_retry_preserves_invalid_response_and_all_attempts(inputs, monkeypatch):
    live_env(monkeypatch)
    client = fake_client(None)
    valid = client.script["analyze_event"][0]
    client.script["analyze_event"] = ["not JSON", valid]
    assert runner().main(args(inputs, "--live", "--case-id", "case-0-0"),
                         client_factory=lambda _: client) == 0
    result = json.loads((inputs["out"] / "cases/case-0-0/result.json").read_text())
    assert result["attempts"][0]["response"]["text"] == "not JSON"
    assert any(event["data"].get("error") for event in result["traces"]["insight"] if event["type"] == "llm_call")
    assert len(result["attempts"]) >= 5


def test_qa_provider_failure_stops_session_but_evidence_refusal_is_valid(inputs, monkeypatch, capsys):
    live_env(monkeypatch)
    client = fake_client(None)
    client.script["answer"] = [LLMError("Synthetic QA provider failure")]
    assert runner().main(args(inputs, "--live"), client_factory=lambda _: client) == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["completed_cases"] == 1
    result = json.loads((inputs["out"] / "cases/case-0-0/result.json").read_text())
    assert result["qa"]["status"] == "no_answer"
    assert any(error["type"] == "qa_execution_failed" for error in result["errors"])


@pytest.mark.parametrize("name", ["cases", "traces"])
def test_output_children_cannot_redirect_private_artifacts(inputs, monkeypatch, tmp_path, name):
    live_env(monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    inputs["out"].mkdir(mode=0o700)
    (inputs["out"] / name).symlink_to(outside, target_is_directory=True)
    assert runner().main(args(inputs, "--live", "--case-id", "case-0-0"),
                         client_factory=forbidden_client) == 2
    assert list(outside.iterdir()) == []
