"""Frozen runtime_wire v1 HTTP contracts; no speculative store/token helper APIs."""
import hashlib
import shutil

from fastapi.testclient import TestClient
import pytest

from fakes import FIXTURES, ScriptedClient
from insight_agent.api import build_agent, create_app
from insight_agent.materials.identity import event_content_sha256
from insight_agent.materials.store import publish_snapshot
from test_corpus_runtime import make_runtime_snapshot, runtime_client, runtime_request, runtime_settings


def catalog_app(tmp_path, request_):
    root = tmp_path / "store"
    snapshot = make_runtime_snapshot(root, request_, "alpha")
    revision = publish_snapshot(root, snapshot)
    provider = runtime_client()
    settings = runtime_settings(tmp_path, root)
    return root, snapshot, revision, provider, TestClient(create_app(build_agent(settings, provider)))


def test_catalog_pages_pin_full_token_and_preserve_events(tmp_path, request_):
    root, snapshot, revision, provider, http = catalog_app(tmp_path, request_)
    first = http.get("/v1/materials/events", params={"limit": 1})
    assert first.status_code == 200
    page = first.json()
    assert set(page) == {"catalog_token", "events", "next_cursor"}
    token = page["catalog_token"]
    assert set(token) == {"store_id", "generation", "revision"}
    assert token["store_id"] and token["generation"] >= 1 and token["revision"] == revision
    assert page["next_cursor"]
    assert [item["event"]["event_id"] for item in page["events"]] == ["evt_demo_rate"]

    second = http.get("/v1/materials/events", params={
        "revision": revision, "cursor": page["next_cursor"], "limit": 1,
    })
    assert second.status_code == 200
    assert second.json()["catalog_token"] == token
    assert second.json()["next_cursor"] is None
    items = page["events"] + second.json()["events"]
    assert [item["event"]["event_id"] for item in items] == ["evt_demo_rate", "evt_related_alpha"]
    for item, event in zip(items, snapshot.news):
        assert set(item) == {"event", "event_content_sha256"}
        assert item["event"]["text"] == event.text
        assert item["event"]["source"]["content_sha256"] == hashlib.sha256(event.text.encode()).hexdigest()
        # Accepted T02 identity is an independent integration contract for this HTTP adapter.
        assert item["event_content_sha256"] == event_content_sha256(event)
    assert provider.calls == []


def test_single_event_returns_same_catalog_envelope(tmp_path, request_):
    root, snapshot, revision, provider, http = catalog_app(tmp_path, request_)
    response = http.get("/v1/materials/events/evt_demo_rate", params={"revision": revision})
    assert response.status_code == 200
    payload = response.json()
    assert payload["catalog_token"]["revision"] == revision
    assert payload["next_cursor"] is None
    assert len(payload["events"]) == 1
    assert payload["events"][0]["event"]["event_id"] == "evt_demo_rate"
    assert payload["events"][0]["event_content_sha256"] == event_content_sha256(snapshot.news[0])
    assert http.get("/v1/materials/events/not-present", params={"revision": revision}).status_code == 404
    assert provider.calls == []


def test_bank_only_publication_advances_catalog_without_changing_event_hash(tmp_path, request_):
    root, old, r1, provider, http = catalog_app(tmp_path, request_)
    before = http.get("/v1/materials/events")
    assert before.status_code == 200
    new = make_runtime_snapshot(root, request_, "beta")
    new.news, new.source_versions = old.news, old.source_versions
    r2 = publish_snapshot(root, new)
    after = http.get("/v1/materials/events")
    assert after.status_code == 200
    old_token, new_token = before.json()["catalog_token"], after.json()["catalog_token"]
    assert old_token["revision"] == r1 and new_token["revision"] == r2 and r1 != r2
    assert old_token["store_id"] == new_token["store_id"]
    assert new_token["generation"] > old_token["generation"]
    assert after.json()["events"] == before.json()["events"]
    assert publish_snapshot(root, new) == r2
    assert http.get("/v1/materials/events").json()["catalog_token"] == new_token
    assert provider.calls == []


def test_old_catalog_and_cursor_never_advance_current_head(tmp_path, request_):
    root, old, r1, provider, http = catalog_app(tmp_path, request_)
    first = http.get("/v1/materials/events", params={"limit": 1})
    assert first.status_code == 200
    old_token, cursor = first.json()["catalog_token"], first.json()["next_cursor"]
    r2 = publish_snapshot(root, make_runtime_snapshot(root, request_, "beta"))
    # A fresh process has no cached R1 token; historical metadata must survive restart.
    http = TestClient(create_app(build_agent(runtime_settings(tmp_path, root), provider)))
    current = http.get("/v1/materials/events")
    assert current.status_code == 200
    current_token = current.json()["catalog_token"]
    pointer = (root / "current.json").read_bytes()

    # A cursor cannot be relabeled with a different/current publication token.
    assert http.get("/v1/materials/events", params={"revision": r2, "cursor": cursor}).status_code == 409
    assert http.get("/v1/materials/events", params={"cursor": cursor}).status_code == 409
    historical = http.get("/v1/materials/events", params={"revision": r1, "cursor": cursor})
    assert historical.status_code == 200
    assert historical.json()["catalog_token"] == old_token
    assert [item["event"]["event_id"] for item in historical.json()["events"]] == ["evt_related_alpha"]
    assert (root / "current.json").read_bytes() == pointer
    assert http.get("/v1/materials/events").json()["catalog_token"] == current_token
    assert provider.calls == []


def test_legacy_readonly_catalog_reports_uninitialized_without_writing_token(tmp_path):
    root = tmp_path / "legacy"
    shutil.copytree(FIXTURES / "materials" / "v1", root)
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    provider = ScriptedClient({})
    with TestClient(create_app(build_agent(runtime_settings(tmp_path, root), provider))) as http:
        health = http.get("/health")
        assert health.status_code == 200
        response = http.get("/v1/materials/events")
        assert response.status_code == 503
        assert "catalog_uninitialized" in response.text
        assert health.json()["catalog_initialized"] is False
        assert health.json()["catalog_token"] is None
    after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert after == before
    assert provider.calls == []


def test_refresh_reloads_only_and_never_calls_provider_or_republishes(tmp_path, request_):
    root, old, r1, provider, http = catalog_app(tmp_path, request_)
    r2 = publish_snapshot(root, make_runtime_snapshot(root, request_, "beta"))
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    refreshed = http.post("/v1/materials/refresh")
    assert refreshed.status_code == 200
    assert http.get("/health").json()["corpus_revision"] == r2
    assert r1 != r2
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert provider.calls == []


@pytest.mark.parametrize("corruption", ["body", "event_hash", "source_hash", "missing_revision"])
def test_pinned_request_integrity_failure_precedes_any_provider_call(tmp_path, request_, corruption):
    root, snapshot, revision, provider, http = catalog_app(tmp_path, request_)
    body = runtime_request(request_, snapshot).model_dump(mode="json") | {
        "corpus_revision": revision, "event_content_sha256": event_content_sha256(snapshot.news[0]),
    }
    if corruption == "body":
        body["event"]["text"] += " This statement was not published in the pinned corpus."
    elif corruption == "event_hash":
        body["event_content_sha256"] = "0" * 64
    elif corruption == "source_hash":
        body["event"]["source"]["content_sha256"] = "0" * 64
    else:
        body["corpus_revision"] = "0" * 64
    response = http.post("/v1/insights", json=body)
    assert (response.status_code, len(provider.calls)) == (409, 0)
    code = "corpus_unavailable" if corruption == "missing_revision" else "corpus_event_mismatch"
    assert code in response.text
    assert provider.calls == []


def test_explicit_revision_without_materials_is_rejected_before_provider(make_agent, request_):
    provider = runtime_client()
    http = TestClient(create_app(make_agent(provider)))
    response = http.post("/v1/insights", json=request_.model_dump(mode="json") | {"corpus_revision": "0" * 64})
    assert (response.status_code, len(provider.calls)) == (409, 0)
    assert "corpus_unavailable" in response.text
    assert provider.calls == []


def test_generated_bank_pdf_link_pins_revision_and_public_origin(tmp_path, request_):
    root, snapshot, revision, provider, http = catalog_app(tmp_path, request_)
    response = http.post("/v1/insights", json=runtime_request(request_, snapshot).model_dump(mode="json"))
    assert response.status_code == 200
    source = next(ref for ref in response.json()["sources"] if ref["source_id"] == "bank_alpha")
    assert source["url"] == (
        f"https://materials.example.test/v1/materials/documents/{snapshot.documents[0].doc_id}"
        f"/file?revision={revision}#page=1"
    )


def test_corrupt_refresh_retains_serving_bundle_and_blocks_new_current_run(tmp_path, request_):
    root, old, r1, provider, http = catalog_app(tmp_path, request_)
    r2 = publish_snapshot(root, make_runtime_snapshot(root, request_, "beta"))
    (root / "snapshots" / r2 / "news.jsonl").write_text("corrupt collection")
    refreshed = http.post("/v1/materials/refresh")
    assert refreshed.status_code == 503
    health = http.get("/health").json()
    assert health["status"] == "degraded"
    assert health["serving_revision"] == r1
    assert health["current_revision"] is None
    assert health["last_refresh_error"]
    response = http.post("/v1/insights", json=runtime_request(request_, old).model_dump(mode="json"))
    assert (response.status_code, len(provider.calls)) == (409, 0)
    # A bad current pointer cannot invalidate independently addressed historical evidence.
    historical = http.post("/v1/insights", json=runtime_request(request_, old).model_dump(mode="json")
                           | {"corpus_revision": r1})
    assert historical.status_code == 200
    assert historical.json()["corpus_revision"] == r1


def test_republished_same_revision_does_not_rebind_an_old_cursor(tmp_path, request_):
    root, old, r1, provider, http = catalog_app(tmp_path, request_)
    first = http.get("/v1/materials/events", params={"limit": 1}).json()
    publish_snapshot(root, make_runtime_snapshot(root, request_, "beta"))
    assert publish_snapshot(root, old) == r1
    current = http.get("/v1/materials/events").json()
    assert current["catalog_token"]["revision"] == r1
    assert current["catalog_token"]["generation"] > first["catalog_token"]["generation"]
    response = http.get("/v1/materials/events", params={"revision": r1, "cursor": first["next_cursor"]})
    assert response.status_code == 409
    assert provider.calls == []


def test_result_saved_context_and_qa_trace_keep_revision_hash_and_cutoff(tmp_path, request_):
    root, snapshot, revision, provider, http = catalog_app(tmp_path, request_)
    request = runtime_request(request_, snapshot, corpus_revision=revision, as_of="2026-10-26T12:00:00Z",
                              event_content_sha256=event_content_sha256(snapshot.news[0]))
    response = http.post("/v1/insights", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    insight = response.json()
    assert insight["corpus_revision"] == revision
    assert insight["event_content_sha256"] == request.event_content_sha256
    assert insight["as_of"] == "2026-10-26T12:00:00Z"
    provider.script["answer"] = [{"answerable": False, "text": "No answer", "claims": []}]
    restarted = TestClient(create_app(build_agent(runtime_settings(tmp_path, root), provider)))
    answer = restarted.post(f'/v1/insights/{insight["insight_id"]}/questions', json={"question": "credit financing?"})
    assert answer.status_code == 200
    for field in ("corpus_revision", "event_content_sha256", "as_of"):
        assert answer.json()[field] == insight[field]
    trace = restarted.get(f'/v1/runs/{answer.json()["run_id"]}/trace').json()
    assert trace[0]["data"]["corpus_revision"] == revision
    assert trace[0]["data"]["event_content_sha256"] == request.event_content_sha256


def test_day_only_news_is_eligible_before_but_not_within_its_day(request_):
    from datetime import UTC, datetime
    from insight_agent.schemas import NewsEvent
    from insight_agent.tools.news import NewsSearchArgs, NewsStore
    data = request_.event.model_dump(mode="json")
    data.update(published_at=None, published_date="2026-10-23", published_precision="date")
    news = NewsStore([NewsEvent.model_validate(data)])
    earlier = NewsSearchArgs(query="ключевая ставка", min_score=0, not_after=datetime(2026, 10, 23, 23, tzinfo=UTC))
    later = earlier.model_copy(update={"not_after": datetime(2026, 10, 24, tzinfo=UTC)})
    assert news.search(earlier) == []
    assert [event.event_id for event in news.search(later)] == ["evt_demo_rate"]


def test_health_reports_corrupt_run_report_separately_from_usable_corpus(tmp_path, request_):
    root, snapshot, revision, provider, http = catalog_app(tmp_path, request_)
    (root / "last_attempt.json").write_text('{"run_id":"missing","report_path":"runs/missing.json","sha256":"bad"}')
    health = http.get("/health").json()
    assert health["corpus_revision"] == revision
    assert health["source_health"] == "failed"
    assert health["status"] == "degraded"
    assert health["report_error"]
    assert provider.calls == []


@pytest.mark.parametrize("revision", ["", "../current", "A" * 64, "0" * 63])
def test_invalid_revision_never_falls_back_to_current(tmp_path, request_, revision):
    root, snapshot, valid, provider, http = catalog_app(tmp_path, request_)
    for route in ("/v1/materials/events", "/v1/materials/events/evt_demo_rate",
                  f"/v1/materials/documents/{snapshot.documents[0].doc_id}/file"):
        assert http.get(route, params={"revision": revision}).status_code == 422
    assert provider.calls == []
