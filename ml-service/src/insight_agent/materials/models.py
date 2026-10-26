"""Typed, offline-serializable corpus; dates of publication and observation differ."""
from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from insight_agent.schemas import BankFragment, Completeness, MaterialType, NewsEvent, PublicationPrecision, Sha256, TimezoneProvenance, UtcOffsetSeconds, source_publication_time

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


class SourceMetadata(BaseModel):
    """Known source publication, with no synthetic midnight or observation fallback."""
    published_at: AwareDatetime | None = None
    published_date: date | None = None
    published_precision: PublicationPrecision = "unknown"
    raw_publication: str | None = None
    source_timezone: str | None = None
    source_utc_offset_seconds: UtcOffsetSeconds | None = None
    timezone_provenance: TimezoneProvenance = "unknown"
    publisher_id: str | None = None
    adapter_id: str | None = None
    discovered_url: str | None = None
    language: str | None = None
    material_type: MaterialType = "news_article"
    completeness: Completeness = "full"
    parser_version: str | None = None
    rights_url: str | None = None
    translation_attribution: str | None = None
    topics: list[str] = Field(default_factory=list)
    original_text: str | None = None
    raw_body: str | None = None

    @model_validator(mode="after")
    def publication_consistency(self):
        if self.timezone_provenance == "source_policy" and not self.source_timezone:
            raise ValueError("Source policy requires an explicit source timezone")
        expected = "timestamp" if self.published_at else "date" if self.published_date else "unknown"
        if "published_precision" in self.model_fields_set and self.published_precision != expected:
            raise ValueError("Publication precision does not match source date/time")
        if self.published_at:
            source_time = source_publication_time(self.published_at,
                source_utc_offset_seconds=self.source_utc_offset_seconds, source_timezone=self.source_timezone)
            if self.published_date and self.published_date != source_time.date():
                raise ValueError("Publication day does not match source timestamp")
            self.published_date = source_time.date()
            # Retain a known source offset for UTC-normalizing consumers. A legacy UTC
            # timestamp with unknown provenance does not reveal the original source offset.
            if self.source_timezone or self.timezone_provenance == "explicit_offset" or source_time.utcoffset().total_seconds() != 0:
                self.source_utc_offset_seconds = int(source_time.utcoffset().total_seconds())
        self.published_precision = expected
        return self


class CatalogToken(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    store_id: str = Field(min_length=1)
    generation: int = Field(ge=0)
    revision: Sha256


class RawNews(SourceMetadata):
    url: str
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    published_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime


class SourceVersion(SourceMetadata):
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
    schema_version: Literal[1, 2] = 2
    pipeline_version: str = "materials-v2"
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
