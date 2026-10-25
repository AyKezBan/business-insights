"""Runtime behavior contracts, using real immutable snapshots and search indexes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
import hashlib
from pathlib import Path
import shutil
from threading import Event

import httpx
from fastapi.testclient import TestClient

from fakes import ScriptedClient, analysis_reply, draft_reply
from insight_agent.agent.qa import answer_question
from insight_agent.agent.state import AgentState
from insight_agent.api import build_agent, create_app
from insight_agent.config import Settings
from insight_agent.materials.models import CorpusSnapshot, PageArtifact, SourceVersion
from insight_agent.materials.store import publish_snapshot
from insight_agent.research.fetch import SafeFetcher
from insight_agent.research.indicators import KEY_RATE_URL
from insight_agent.research.service import OpenResearch, PRESS_FEED_URL, TtlCache
from insight_agent.schemas import BankFragment, InsightRequest, NewsEvent, SourceRef
from insight_agent.tools.profile import derive_signals
from insight_agent.trace import Tracer, load_trace
from test_pdf_extraction import make_pdf


OBSERVED = datetime(2026, 10, 23, 14, tzinfo=UTC)
BANK_TEXT = {
    "alpha": "Corporate credit financing remains costly according to the bank alpha.",
    "beta": "Corporate credit financing becomes cheaper according to the bank beta.",
}
NEWS_TEXT = {
    "alpha": "Corporate credit funding remains tight in the alpha bulletin.",
    "beta": "Corporate credit funding becomes easier in the beta bulletin.",
}


def make_runtime_snapshot(root: Path, request: InsightRequest, marker: str,
                          *, bank_day: date = date(2026, 10, 22)) -> CorpusSnapshot:
    """Small valid corpus: actual PDF/page artifact, one fragment and two news items."""
    root.mkdir(parents=True, exist_ok=True)
    text = BANK_TEXT[marker]
    document = make_pdf(root / f"{marker}.pdf", text).model_copy(update={"doc_date": bank_day})
    page_path = root / f"{marker}.txt"
    page_path.write_text(text, encoding="utf-8")
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    page = PageArtifact(doc_id=document.doc_id, page=1, extraction_revision=document.sha256,
                        relative_text_path=page_path.name, text_sha256=text_hash,
                        method="text", status="ready")
    fragment = BankFragment(fragment_id=f"bank_{marker}", doc_id=document.doc_id,
                            doc_title="Corporate credit financing", doc_date=bank_day, page=1,
                            text=text, content_sha256=text_hash, char_start=0, char_end=len(text),
                            review_status="verified", statement_type="bank_assessment")

    primary = request.event.model_copy(deep=True)
    primary.source = primary.source.model_copy(update={
        "content_sha256": hashlib.sha256(primary.text.encode()).hexdigest(),
        "first_seen_at": OBSERVED, "retrieved_at": OBSERVED,
    })
    related = NewsEvent(
        event_id=f"evt_related_{marker}", title="Corporate credit funding bulletin",
        text=NEWS_TEXT[marker], published_at=datetime(2026, 10, 22, 12, tzinfo=UTC),
        source=SourceRef(source_id=f"news_{marker}", kind="news", title="Credit bulletin",
                         url=f"https://example.org/{marker}",
                         content_sha256=hashlib.sha256(NEWS_TEXT[marker].encode()).hexdigest(),
                         first_seen_at=OBSERVED, retrieved_at=OBSERVED),
    )
    versions = [SourceVersion(
        source_id=event.source.source_id, event_id=event.event_id, url=event.source.url,
        revision=1, content_sha256=event.source.content_sha256,
        published_at=event.published_at, published_date=event.published_date,
        first_seen_at=OBSERVED, retrieved_at=OBSERVED, text=event.text, title=event.title,
    ) for event in (primary, related)]
    return CorpusSnapshot(documents=[document], pages=[page], bank_fragments=[fragment],
                          news=[primary, related], source_versions=versions)


def runtime_settings(tmp_path: Path, root: Path) -> Settings:
    return Settings(_env_file=None, materials_dir=root,
                    materials_public_base_url="https://materials.example.test",
                    open_research_enabled=False, trace_dir=tmp_path / "traces",
                    agent_max_iterations=1)


def runtime_request(request: InsightRequest, snapshot: CorpusSnapshot, **updates) -> InsightRequest:
    return InsightRequest.model_validate(request.model_dump(mode="json") | {
        "event": snapshot.news[0].model_dump(mode="json"), **updates,
    })


def runtime_client() -> ScriptedClient:
    def draft(messages):
        source_context = messages[-1]["content"]
        reply = draft_reply(with_bank=False)
        for marker in ("beta", "alpha"):
            if f"source_id: bank_{marker}\n" in source_context:
                reply["bank_view"] = [{"text": BANK_TEXT[marker], "kind": "assessment",
                                       "source_ids": [f"bank_{marker}"]}]
                break
        return reply

    return ScriptedClient({
        "analyze_event": [analysis_reply()],
        "plan": [{"steps": [
            {"tool": "search_bank_analytics", "args": {"query": "credit financing", "min_score": 0},
             "purpose": "bank evidence"},
            {"tool": "search_news", "args": {"query": "credit funding", "min_score": 0,
                                                "exclude_event_id": "evt_demo_rate"},
             "purpose": "related news"},
        ]}],
        "personalize": [draft],
    })


def retrieved_ids(settings, insight, tool):
    events = load_trace(settings.trace_dir, insight.run_id)
    calls = [event.data["result"] for event in events
             if event.type == "tool_call" and event.data.get("tool") == tool and "result" in event.data]
    if tool == "search_bank_analytics":
        return {fragment["id"] for result in calls for fragment in result["fragments"]}
    return {event_id for result in calls for event_id in result["events"]}


def test_inflight_tools_keep_r1_while_a_new_run_uses_r2(tmp_path, request_, monkeypatch):
    root = tmp_path / "store"
    old = make_runtime_snapshot(root, request_, "alpha")
    r1 = publish_snapshot(root, old)
    new = make_runtime_snapshot(root, request_, "beta")
    settings = runtime_settings(tmp_path, root)
    provider = runtime_client()
    agent = build_agent(settings, provider)
    at_tool_boundary, continue_run = Event(), Event()
    emit = Tracer.emit

    def observe_tool(tracer, type_, step, data=None):
        event = emit(tracer, type_, step, data)
        if (type_ == "tool_call" and (data or {}).get("tool") == "search_bank_analytics"
                and "result" in data and not at_tool_boundary.is_set()):
            at_tool_boundary.set()
            if not continue_run.wait(5):
                raise AssertionError("Test did not release the real tool-result barrier")
        return event

    # Pass-through observer only: real tool/index/store results are unchanged.
    monkeypatch.setattr(Tracer, "emit", observe_tool)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(agent.run, runtime_request(request_, old))
        try:
            assert at_tool_boundary.wait(5), "Run A never reached the bank tool boundary"
            r2 = publish_snapshot(root, new)
            # A remains paused; B may refresh the active bundle but must not mutate A's tools.
            second = agent.run(runtime_request(request_, new))
        finally:
            continue_run.set()
        first = pending.result(timeout=5)

    assert r1 != r2
    assert retrieved_ids(settings, first, "search_bank_analytics") == {"bank_alpha"}
    assert retrieved_ids(settings, first, "search_news") == {"evt_related_alpha"}
    assert first.bank_view[0].text == BANK_TEXT["alpha"]
    assert retrieved_ids(settings, second, "search_bank_analytics") == {"bank_beta"}
    assert retrieved_ids(settings, second, "search_news") == {"evt_related_beta"}
    assert second.bank_view[0].text == BANK_TEXT["beta"]
    assert provider.steps().count("personalize") == 2


def test_day_only_event_uses_its_known_day_for_bank_cutoff(tmp_path, request_):
    data = request_.model_dump(mode="json")
    data["event"].update(published_at=None, published_date="2026-10-23", published_precision="date")
    day_request = InsightRequest.model_validate(data | {"as_of": "2026-10-26T12:00:00Z"})
    root = tmp_path / "store"
    old = make_runtime_snapshot(root, day_request, "alpha")
    future = make_runtime_snapshot(root, day_request, "beta", bank_day=date(2026, 10, 24))
    old.documents += future.documents
    old.pages += future.pages
    old.bank_fragments += future.bank_fragments
    publish_snapshot(root, old)
    settings = runtime_settings(tmp_path, root)
    provider = runtime_client()
    insight = build_agent(settings, provider).run(runtime_request(day_request, old))

    assert retrieved_ids(settings, insight, "search_bank_analytics") == {"bank_alpha"}
    assert insight.bank_view[0].text == BANK_TEXT["alpha"]
    assert all(source.source_id != "bank_beta" for source in insight.sources)


def test_indicator_effective_day_is_not_invented_as_publication_midnight(tmp_path, request_):
    root = tmp_path / "store"
    snapshot = make_runtime_snapshot(root, request_, "alpha")
    publish_snapshot(root, snapshot)
    agent = build_agent(runtime_settings(tmp_path, root), runtime_client())
    now = datetime(2026, 10, 26, 12, tzinfo=UTC)
    cache = TtlCache(clock=lambda: now)
    # Real research parser reads real cached source bytes; HTTP is prohibited even on a cache miss.
    cache.set(cache.key(KEY_RATE_URL, {"UniDbQuery.Posted": "True", "UniDbQuery.From": "26.04.2026",
                                      "UniDbQuery.To": "23.10.2026"}),
              b"<table><tr><td>23.10.2026</td><td>14.00</td></tr></table>")
    cache.set(cache.key(PRESS_FEED_URL, None), b"<rss><channel></channel></rss>")

    def deny_network(request):
        raise AssertionError(f"Unexpected uncached research HTTP: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(deny_network)) as http:
        agent.research = OpenResearch(SafeFetcher(http), cache, clock=lambda: now)
        profile = request_.profile.model_copy(update={"foreign_trade": False, "foreign_trade_countries": []})
        request = runtime_request(request_, snapshot, profile=profile.model_dump(mode="json"))
        tracer, _ = agent.new_run()
        state = AgentState(run_id=tracer.run_id, request=request,
                           signals=derive_signals(profile), missing_profile=[])
        found = agent.tools_for(state, tracer).call("search_open_sources", {}, step="indicator_precision")

    assert len(found) == 1
    indicator = found[0]
    assert indicator.published_at is None
    assert indicator.published_date is None
    assert indicator.source.material_type == "indicator"
    assert indicator.source.retrieved_at == now
    assert "23.10.2026" in indicator.text  # The effective day is retained as effective evidence.
    assert "14,00%" in indicator.text


def test_historical_pdf_is_available_after_restart_and_removal_from_current(tmp_path, request_):
    root = tmp_path / "store"
    old = make_runtime_snapshot(root, request_, "alpha")
    r1 = publish_snapshot(root, old)
    original = (root / old.documents[0].relative_path).read_bytes()
    new = make_runtime_snapshot(root, request_, "beta")
    r2 = publish_snapshot(root, new)
    restarted = build_agent(runtime_settings(tmp_path, root), ScriptedClient({}))
    route = f"/v1/materials/documents/{old.documents[0].doc_id}/file"

    with TestClient(create_app(restarted)) as http:
        response = http.get(route, params={"revision": r1})
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content == original
        assert http.get(route, params={"revision": r2}).status_code == 404
        assert http.get(route, params={"revision": "0" * 64}).status_code == 404


def test_restarted_qa_searches_saved_revision_instead_of_new_current(tmp_path, request_):
    root = tmp_path / "store"
    old = make_runtime_snapshot(root, request_, "alpha")
    r1 = publish_snapshot(root, old)
    settings = runtime_settings(tmp_path, root)
    first = build_agent(settings, runtime_client()).run(runtime_request(request_, old))
    publish_snapshot(root, make_runtime_snapshot(root, request_, "beta"))

    def answer(messages):
        context = messages[-1]["content"]
        marker = "beta" if "source_id: bank_beta\n" in context else "alpha"
        return {"answerable": True, "text": BANK_TEXT[marker], "claims": [
            {"text": BANK_TEXT[marker], "kind": "assessment", "source_ids": [f"bank_{marker}"]},
        ]}

    provider = ScriptedClient({"answer": [answer]})
    response = answer_question(build_agent(settings, provider), first.insight_id, "Corporate credit financing?")
    assert response.status == "answered"
    assert response.text == BANK_TEXT["alpha"]
    assert {source.source_id for source in response.sources} == {"bank_alpha"}
    assert response.model_dump().get("corpus_revision") == r1
    assert provider.steps() == ["answer"]


def test_missing_saved_revision_refuses_qa_without_provider_call(tmp_path, request_):
    root = tmp_path / "store"
    old = make_runtime_snapshot(root, request_, "alpha")
    r1 = publish_snapshot(root, old)
    settings = runtime_settings(tmp_path, root)
    first = build_agent(settings, runtime_client()).run(runtime_request(request_, old))
    publish_snapshot(root, make_runtime_snapshot(root, request_, "beta"))
    shutil.rmtree(root / "snapshots" / r1)
    provider = ScriptedClient({"answer": [{"answerable": False, "text": "No answer", "claims": []}]})

    response = answer_question(build_agent(settings, provider), first.insight_id, "Corporate credit financing?")
    assert response.status == "no_answer"
    assert provider.calls == []
    assert any(r1 in reason for reason in response.missing_data)
    assert response.model_dump().get("corpus_revision") == r1


def test_legacy_no_materials_request_still_runs(make_agent, request_):
    provider = runtime_client()
    insight = make_agent(provider).run(request_)
    assert insight.status != "failed"
    assert insight.model_dump().get("corpus_revision") is None
    assert provider.steps().count("personalize") == 1
