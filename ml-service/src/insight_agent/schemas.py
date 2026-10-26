import hashlib
import json
from datetime import UTC, date as Date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

UtcOffsetSeconds = Annotated[int, Field(gt=-86400, lt=86400)]
TimezoneProvenance = Literal["explicit_offset", "source_policy", "unknown"]

PublicationPrecision = Literal["timestamp", "date", "unknown"]
MaterialType = Literal["news_article", "public_analysis", "publisher_annotation", "indicator"]
Completeness = Literal["full", "partial"]

DISCLAIMER = (
    "Аналитика носит информационный характер и не является инвестиционной рекомендацией. "
    "Выводы с пометкой «вывод модели» и «гипотеза» сформированы AI и не являются позицией Альфа-Банка."
)


class UserRole(StrEnum):
    owner = "owner"
    ceo = "ceo"
    cfo = "cfo"
    chief_accountant = "chief_accountant"
    treasurer = "treasurer"
    other_c_level = "other_c_level"
    other = "other"


class ClaimKind(StrEnum):
    fact = "fact"
    assessment = "assessment"
    inference = "inference"
    hypothesis = "hypothesis"
    recommendation = "recommendation"
    unavailable = "unavailable"


class Outcome(StrEnum):
    full = "full"
    limited = "limited"
    refused = "refused"
    none = "none"


class Level(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class SourceKind(StrEnum):
    news = "news"
    bank = "bank"
    profile = "profile"


class Products(BaseModel):
    settlement: bool | None = None
    guarantees: bool | None = None
    loans: bool | None = None
    leasing: bool | None = None
    deposits: bool | None = None
    payroll: bool | None = None
    factoring: bool | None = None


class Counterparty(BaseModel):
    company_id: str
    direction: str
    amount_rub: Decimal | None = None
    share_of_flow: float | None = None
    okved_code: str | None = None
    okved_name: str | None = None
    region: str | None = None
    same_group: bool | None = None


class CompanyProfile(BaseModel):
    company_id: str
    group_id: str | None = None
    okved_code: str | None = None
    okved_name: str | None = None
    industry: str | None = None
    revenue_rub: Decimal | None = None
    group_revenue_rub: Decimal | None = None
    revenue_year: int | None = None
    revenue_status: Literal["known", "placeholder", "missing", "invalid"] | None = None
    revenue_scope: Literal["company", "group", "unknown"] | None = None
    foreign_trade_data_available: bool | None = None
    payment_period: str | None = None
    profile_quality_notes: list[str] = Field(default_factory=list)
    employees: str | None = None
    company_age: str | None = None
    business_stage: str | None = None
    ownership: str | None = None
    region: str | None = None
    city: str | None = None
    client_type: str | None = None
    products: Products = Field(default_factory=Products)
    foreign_trade: bool | None = None
    foreign_trade_countries: list[str] = Field(default_factory=list)
    counterparties: list[Counterparty] = Field(default_factory=list)
    group_member_okveds: list[str] = Field(default_factory=list)
    self_transfer_share: float | None = None
    user_provided: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)

    @field_validator("revenue_status", mode="before")
    @classmethod
    def accept_import_vocabulary(cls, value):
        # data-import (Backend) называет известную выручку observed, в ML контракте это known.
        return "known" if isinstance(value, str) and value.strip().lower() == "observed" else value

    @model_validator(mode="after")
    def preserve_unknowns(self):
        if self.revenue_rub == 1000:
            self.revenue_rub = None
            self.revenue_status = "placeholder"
        if self.revenue_status in ("placeholder", "missing", "invalid"):
            self.revenue_rub = None
        if self.foreign_trade_data_available is False:
            self.foreign_trade = None
        return self


def source_publication_time(published_at: datetime, *, source_utc_offset_seconds: int | None = None,
                            source_timezone: str | None = None) -> datetime:
    """Project an instant onto the source clock using retained evidence, not a UTC day guess."""
    if source_timezone:
        try:
            localized = published_at.astimezone(ZoneInfo(source_timezone))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown source timezone") from exc
        if source_utc_offset_seconds is not None and localized.utcoffset().total_seconds() != source_utc_offset_seconds:
            raise ValueError("Source offset does not match source timezone at publication")
        return localized
    if source_utc_offset_seconds is not None:
        return published_at.astimezone(timezone(timedelta(seconds=source_utc_offset_seconds)))
    return published_at


class SourceRef(BaseModel):
    source_id: str
    kind: SourceKind
    title: str
    date: Date | None = None
    page: int | None = Field(default=None, ge=1)
    url: str | None = None
    doc_id: str | None = None
    content_sha256: Sha256 | None = None
    excerpt: str | None = None
    publisher_id: str | None = None
    adapter_id: str | None = None
    material_type: MaterialType | None = None
    completeness: Completeness | None = None
    language: str | None = None
    published_date: Date | None = None
    published_precision: PublicationPrecision | None = None
    raw_publication: str | None = None
    source_timezone: str | None = None
    source_utc_offset_seconds: UtcOffsetSeconds | None = None
    timezone_provenance: TimezoneProvenance | None = None
    first_seen_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime | None = None
    statement_type: str | None = None
    target_period: str | None = None
    valid_until: Date | None = None
    rights_url: str | None = None
    translation_attribution: str | None = None

    @model_validator(mode="after")
    def publication_day_consistency(self):
        if self.published_date and self.date and self.published_date != self.date:
            raise ValueError("Source reference publication day declarations disagree")
        return self


class SourceConflict(BaseModel):
    reason: str
    source_ids: list[str]
    resolved: bool = False


class NewsEvent(BaseModel):
    event_id: str
    title: str
    text: str
    published_at: AwareDatetime | None = None
    published_date: Date | None = None
    published_precision: PublicationPrecision | None = None
    source: SourceRef
    related_sources: list[SourceRef] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    conflicts: list[SourceConflict] = Field(default_factory=list)

    @model_validator(mode="after")
    def external_source_kind(self):
        if self.published_date and self.source.published_date and self.published_date != self.source.published_date:
            raise ValueError("Event and primary source publication day declarations disagree")
        if self.published_precision is not None:
            expected = "timestamp" if self.published_at else "date" if self.published_date else "unknown"
            if self.published_precision != expected:
                raise ValueError("Publication precision does not match event date/time")
        if self.published_at and self.published_date:
            source_time = source_publication_time(self.published_at,
                source_utc_offset_seconds=self.source.source_utc_offset_seconds,
                source_timezone=self.source.source_timezone)
            if self.published_date != source_time.date():
                raise ValueError("Publication day does not match event source timestamp")
        if any(ref.kind != SourceKind.news for ref in [self.source] + self.related_sources):
            raise ValueError("NewsEvent references must have kind=news; bank evidence is retrieved separately")
        return self


class NewsPage(BaseModel):
    corpus_revision: str | None = None
    items: list[NewsEvent]
    next_offset: int | None = Field(default=None, ge=0)


class BankFragment(BaseModel):
    fragment_id: str
    doc_id: str
    doc_title: str
    doc_date: Date | None = None
    page: int | None = Field(default=None, ge=1)
    text: str
    topics: list[str] = Field(default_factory=list)
    source_url: str | None = None
    content_sha256: Sha256 | None = None
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    extraction_method: str | None = None
    review_status: Literal["pending", "verified", "rejected"] = "pending"
    statement_type: Literal["reported_fact", "bank_assessment", "forecast", "mixed"] = "mixed"
    target_period: str | None = None
    valid_until: Date | None = None
    supersedes: list[str] = Field(default_factory=list)


class InsightRequest(BaseModel):
    profile: CompanyProfile
    role: UserRole = UserRole.ceo
    event: NewsEvent
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    as_of: datetime | None = None
    corpus_revision: Sha256 | None = None
    event_content_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def fixed_time_slice(self):
        if self.as_of is not None:
            if self.as_of.tzinfo is None:
                self.as_of = self.as_of.replace(tzinfo=UTC)
            published = self.event.published_at
            if published is not None:
                published = published if published.tzinfo else published.replace(tzinfo=UTC)
                if published > self.as_of:
                    raise ValueError("event.published_at is later than as_of")
            elif self.event.published_date and self.event.published_date > self.as_of.date():
                raise ValueError("event.published_date is later than as_of")
        return self


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class EvidenceSpan(BaseModel):
    """Offsets in the exact source body, measured in Unicode code points."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, strict=True)
    content_sha256: Sha256
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.end <= self.start:
            raise ValueError("Evidence span must be nonempty and ordered")
        return self

    @property
    def span_id(self) -> str:
        identity = json.dumps([self.source_id, self.content_sha256, self.start, self.end],
                              ensure_ascii=False, separators=(",", ":"))
        return "span:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


class Claim(BaseModel):
    claim_id: str | None = None
    text: str
    kind: ClaimKind
    source_ids: list[str] = Field(default_factory=list)
    profile_features: list[str] = Field(default_factory=list)
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)


class Impact(Claim):
    level: Level
    horizon: str | None = None


class Scenario(BaseModel):
    name: str
    trigger: str
    implication: str
    plausibility: Level
    source_ids: list[str] = Field(default_factory=list)


class EventAnalysis(BaseModel):
    event_id: str
    summary: str
    facts: list[Claim]
    actors: list[str] = Field(default_factory=list)
    impact_channels: list[str] = Field(default_factory=list)
    bank_queries: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class PersonalizationDraft(BaseModel):
    headline: str
    why_it_matters: list[Claim]
    consequences: list[Claim]
    risks: list[Impact]
    opportunities: list[Impact]
    scenarios: list[Scenario] = Field(default_factory=list)
    actions: list[Claim]
    bank_view: list[Claim]
    model_vs_bank: list[str] = Field(default_factory=list)
    impact_level: Level
    impact_rationale: str
    missing_data: list[str] = Field(default_factory=list)
    # BR-01: связь события с этой компанией. none - события нет дела до компании, карточка не станет персональным инсайтом.
    company_link: Literal["clear", "conditional", "none"] = "clear"


class CheckIssue(BaseModel):
    code: str
    severity: str
    message: str
    location: str | None = None


class SelfCheckReport(BaseModel):
    passed: bool
    issues: list[CheckIssue] = Field(default_factory=list)
    iterations: int = 1
    adaptations: list[str] = Field(default_factory=list)


class InsightStatus(StrEnum):
    complete = "complete"
    limited = "limited"
    failed = "failed"


class Insight(BaseModel):
    insight_id: str
    run_id: str
    event_id: str
    company_id: str
    role: UserRole
    version: int = 1
    status: InsightStatus
    outcome: Outcome = Outcome.none
    error_code: str | None = None
    request_id: str | None = None
    parent_run_id: str | None = None
    cache_hit: bool = False
    corpus_revision: Sha256 | None = None
    event_content_sha256: Sha256 | None = None
    as_of: datetime | None = None
    headline: str
    what_happened: list[Claim]
    why_it_matters: list[Claim]
    consequences: list[Claim]
    risks: list[Impact]
    opportunities: list[Impact]
    scenarios: list[Scenario]
    actions: list[Claim]
    bank_view: list[Claim]
    model_vs_bank: list[str]
    impact_level: Level
    impact_rationale: str
    profile_features_used: list[str]
    sources: list[SourceRef]
    missing_data: list[str]
    self_check: SelfCheckReport
    models: list[str]
    generated_at: datetime
    disclaimer: str = DISCLAIMER


class Answer(BaseModel):
    insight_id: str
    run_id: str
    company_id: str | None = None
    event_id: str | None = None
    corpus_revision: Sha256 | None = None
    event_content_sha256: Sha256 | None = None
    as_of: datetime | None = None
    status: str
    text: str
    claims: list[Claim] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class TraceEvent(BaseModel):
    run_id: str
    seq: int
    ts: datetime
    type: str
    step: str
    data: dict = Field(default_factory=dict)
