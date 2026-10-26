"""Trusted catalog reads and revision-addressed original documents; no publication."""
import base64
import binascii

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from insight_agent.materials.identity import event_content_sha256
from insight_agent.materials.models import CatalogToken
from insight_agent.materials.runtime import CorpusError, CorpusRegistry
from insight_agent.materials.store import load_catalog_token, resolve_pdf
from insight_agent.schemas import NewsEvent, Sha256


class MaterialEvent(BaseModel):
    event: NewsEvent
    event_content_sha256: Sha256


class CatalogResponse(BaseModel):
    catalog_token: CatalogToken
    events: list[MaterialEvent]
    next_cursor: str | None = None


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    catalog_token: CatalogToken
    after: str


def create_material_router(registry: CorpusRegistry) -> APIRouter:
    router = APIRouter()

    def catalog(revision):
        try:
            bundle = registry.get(revision) if revision else registry.current()
            token = load_catalog_token(registry.root, revision) if revision else bundle.token
        except KeyError:
            raise HTTPException(503, {"code": "catalog_uninitialized"}) from None
        except (CorpusError, OSError, ValueError, TypeError):
            raise HTTPException(503, {"code": "corpus_unavailable"}) from None
        if token is None or token.generation == 0 or token.store_id == "legacy":
            raise HTTPException(503, {"code": "catalog_uninitialized"})
        return bundle, token

    @router.get("/v1/materials/events", response_model=CatalogResponse)
    def events(revision: Sha256 | None = None, cursor: str | None = Query(default=None, max_length=4096),
               limit: int = Query(default=50, ge=1, le=100)):
        bundle, token = catalog(revision)
        ordered = sorted(bundle.news.events, key=lambda event: event.event_id)
        start = 0
        if cursor:
            try:
                raw = base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
                saved = _Cursor.model_validate_json(raw)
                if saved.version != 1 or saved.catalog_token != token:
                    raise ValueError("Cursor belongs to another publication")
                start = next(i + 1 for i, event in enumerate(ordered) if event.event_id == saved.after)
            except (ValueError, UnicodeError, binascii.Error, StopIteration):
                raise HTTPException(409, {"code": "catalog_cursor_mismatch"}) from None
        selected = ordered[start:start + limit]
        next_cursor = None
        if start + limit < len(ordered):
            saved = _Cursor(version=1, catalog_token=token, after=selected[-1].event_id)
            next_cursor = base64.urlsafe_b64encode(saved.model_dump_json().encode()).decode("ascii")
        return CatalogResponse(catalog_token=token,
            events=[MaterialEvent(event=event, event_content_sha256=event_content_sha256(event)) for event in selected],
            next_cursor=next_cursor)

    @router.get("/v1/materials/events/{event_id}", response_model=CatalogResponse)
    def event(event_id: str, revision: Sha256 | None = None):
        bundle, token = catalog(revision)
        found = next((event for event in bundle.news.events if event.event_id == event_id), None)
        if found is None:
            raise HTTPException(404, "Событие отсутствует в этой версии корпуса")
        return CatalogResponse(catalog_token=token,
            events=[MaterialEvent(event=found, event_content_sha256=event_content_sha256(found))])

    @router.post("/v1/materials/refresh")
    def refresh():
        try:
            bundle = registry.current()
        except CorpusError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from None
        return {"corpus_revision": bundle.revision,
                "catalog_token": bundle.token if bundle.token and bundle.token.generation else None}

    @router.get("/v1/materials/documents/{doc_id}/file")
    def pdf(doc_id: str, revision: Sha256 | None = None):
        try:
            bundle = registry.get(revision) if revision else registry.current()
            path = resolve_pdf(registry.root, bundle.snapshot, doc_id)
        except (KeyError, ValueError, OSError):
            raise HTTPException(404, "Документ недоступен в этой версии корпуса") from None
        return FileResponse(path, media_type="application/pdf")

    return router
