"""Typed, offline-serializable corpus; dates of publication and observation differ."""
from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from insight_agent.schemas import BankFragment, NewsEvent, Sha256

PageStatus = Literal["ready", "empty", "needs_review", "failed"]
ExtractionMethod = Literal["text", "tesseract"]


class DocumentMetadata(BaseModel):
    doc_id: str
    title: str
    doc_date: date | None = None
    reported_period: str | None = None
    sha256: Sha256
    page_count: int = Field(ge=1)
    relative_path: str
    language: str = "rus+eng"
    topics: list[str] = Field(default_factory=list)


class PageText(BaseModel):
    doc_id: str
    page: int = Field(ge=1)
    text: str
    method: ExtractionMethod
    status: PageStatus
    errors: list[str] = Field(default_factory=list)


class PageArtifact(BaseModel):
    doc_id: str
    page: int = Field(ge=1)
    extraction_revision: Sha256
    relative_text_path: str | None = None
    text_sha256: Sha256 | None = None
    method: ExtractionMethod
    status: PageStatus
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_readable_artifact(self):
        if self.status in ("ready", "needs_review") and not (self.relative_text_path and self.text_sha256):
            raise ValueError("Readable page requires a path and text hash")
        return self


class RawNews(BaseModel):
    url: str
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    published_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime


class SourceVersion(BaseModel):
    source_id: str
    event_id: str
    url: str
    revision: int = Field(ge=1)
    active: bool = True
    content_sha256: Sha256
    published_at: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    retrieved_at: AwareDatetime
    text: str
    title: str = ""  # preserve each publisher's title when sources are grouped


class ImportReport(BaseModel):
    documents: int = 0
    pages: int = 0
    fragments: int = 0
    news: int = 0
    errors: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)


class CorpusSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    pipeline_version: str = "materials-v1"
    tool_versions: dict[str, str] = Field(default_factory=dict)
    revision: str = ""
    documents: list[DocumentMetadata] = Field(default_factory=list)
    pages: list[PageArtifact] = Field(default_factory=list)
    bank_fragments: list[BankFragment] = Field(default_factory=list)
    news: list[NewsEvent] = Field(default_factory=list)
    source_versions: list[SourceVersion] = Field(default_factory=list)
    report: ImportReport = Field(default_factory=ImportReport)


class NewsImportResult(BaseModel):
    events: list[NewsEvent]
    versions: list[SourceVersion]
