"""Offset news pagination must coexist with the frozen cursor catalog."""
import pytest

from insight_agent.materials.store import publish_snapshot
from test_corpus_runtime import make_runtime_snapshot
from test_material_catalog import catalog_app


def test_news_and_catalog_have_distinct_routes_and_envelopes(tmp_path, request_):
    _, snapshot, revision, provider, http = catalog_app(tmp_path, request_)
    paths = http.app.openapi()["paths"]
    schemas = {
        path: paths[path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        for path in ("/v1/materials/events", "/v1/materials/news")
    }
    assert schemas["/v1/materials/events"].endswith("/CatalogResponse")
    assert schemas["/v1/materials/news"].endswith("/NewsPage")
    news = http.get("/v1/materials/news", params={"limit": 1}).json()
    assert set(news) == {"corpus_revision", "items", "next_offset"}
    assert news["corpus_revision"] == revision
    ordered = sorted(snapshot.news, key=lambda event: (
        event.published_at.timestamp() if event.published_at else float("-inf"), event.event_id), reverse=True)
    assert news["items"][0]["event_id"] == ordered[0].event_id
    assert news["next_offset"] == 1
    rest = http.get("/v1/materials/news", params={"revision": revision, "offset": 1}).json()
    assert rest["next_offset"] is None
    assert rest["items"][0]["event_id"] == ordered[1].event_id
    assert set(http.get("/v1/materials/events").json()) == {"catalog_token", "events", "next_cursor"}
    assert provider.calls == []


def test_news_pages_can_pin_history_after_runtime_refresh(tmp_path, request_):
    root, _, r1, provider, http = catalog_app(tmp_path, request_)
    before = http.get("/v1/materials/news").json()
    new = make_runtime_snapshot(root, request_, "beta")
    r2 = publish_snapshot(root, new)
    assert r2 != r1
    assert http.get("/v1/materials/news").json()["corpus_revision"] == r2
    assert http.get("/v1/materials/news", params={"revision": r1}).json() == before
    failure = http.get("/v1/materials/news", params={"revision": "f" * 64})
    assert failure.status_code == 503
    assert failure.json()["detail"]["code"] == "corpus_unavailable"
    assert provider.calls == []


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 1000001}, {"revision": "invalid"},
])
def test_news_rejects_invalid_pagination(tmp_path, request_, params):
    _, _, _, provider, http = catalog_app(tmp_path, request_)
    assert http.get("/v1/materials/news", params=params).status_code == 422
    assert provider.calls == []
