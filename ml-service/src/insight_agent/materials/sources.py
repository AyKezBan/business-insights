"""Shared adapter contracts. Registry IDs are distinct from immutable source-version IDs."""
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from insight_agent.materials.models import RawNews
from insight_agent.schemas import MaterialType


class SourceLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    max_bytes: int = Field(default=2 * 1024 * 1024, gt=0, le=2 * 1024 * 1024)
    timeout: float = Field(default=10.0, gt=0, le=10)
    max_redirects: int = Field(default=4, ge=0, le=4)
    min_interval: float = Field(default=2.0, ge=2)
    concurrency: Literal[1] = 1
    retries: int = Field(default=1, ge=0, le=1)
    retry_after_cap: float = Field(default=30.0, ge=0, le=30)


class SourceSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    source_id: str
    publisher_id: str
    hosts: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    discovery_url: str
    parser_version: str
    language: str
    material_type: MaterialType
    date_policy: Literal['explicit_offset', 'day_only', 'source_timezone']
    source_timezone: str | None = None
    rights_url: str
    limits: SourceLimits = Field(default_factory=SourceLimits)


class DiscoveredArticle(BaseModel):
    url: str
    title: str
    raw_date: str | None = None


class FetchBatch(BaseModel):
    source_id: str
    records: list[RawNews] = Field(default_factory=list)
    status: Literal['ok', 'partial', 'empty', 'unchanged', 'blocked', 'failed']
    attempted: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    item_errors: list[str] = Field(default_factory=list)
    checked_at: AwareDatetime | None = None
    last_success_at: AwareDatetime | None = None
    reason: str | None = None
    etag: str | None = None
    last_modified: str | None = None


class NewsSource(Protocol):
    def fetch(self, limit: int) -> list[RawNews]: ...


class NewsSourceAdapter(Protocol):
    def collect(self, limit: int) -> FetchBatch: ...
