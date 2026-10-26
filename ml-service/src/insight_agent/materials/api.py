from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from insight_agent.materials.models import CorpusSnapshot
from insight_agent.materials.store import resolve_pdf


def create_material_router(root: Path, snapshot: CorpusSnapshot) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/materials/documents/{doc_id}/file")
    def pdf(doc_id: str):
        try:
            path = resolve_pdf(root, snapshot, doc_id)
        except (KeyError, ValueError, OSError):
            raise HTTPException(404, "Документ недоступен") from None
        return FileResponse(path, media_type="application/pdf")

    return router
