from datetime import date as Date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

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
    amount_rub: float | None = None
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
    revenue_rub: float | None = None
    group_revenue_rub: float | None = None
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


class SourceRef(BaseModel):
    source_id: str
    kind: SourceKind
    title: str
    date: Date | None = None
    page: int | None = None
    url: str | None = None


class NewsEvent(BaseModel):
    event_id: str
    title: str
    text: str
    published_at: datetime | None = None
    source: SourceRef
    related_sources: list[SourceRef] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)


class BankFragment(BaseModel):
    fragment_id: str
    doc_id: str
    doc_title: str
    doc_date: Date | None = None
    page: int | None = None
    text: str
    topics: list[str] = Field(default_factory=list)


class InsightRequest(BaseModel):
    profile: CompanyProfile
    role: UserRole = UserRole.ceo
    event: NewsEvent


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class Claim(BaseModel):
    text: str
    kind: ClaimKind
    source_ids: list[str] = Field(default_factory=list)
    profile_features: list[str] = Field(default_factory=list)


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
    status: InsightStatus
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
