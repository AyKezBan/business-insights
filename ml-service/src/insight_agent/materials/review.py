import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from insight_agent.schemas import BankFragment, Sha256


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_sha256: Sha256
    review_status: Literal["verified", "rejected"]
    statement_type: Literal["reported_fact", "bank_assessment", "forecast", "mixed"] = "mixed"
    target_period: str | None = None
    valid_until: date | None = None
    supersedes: list[str] = Field(default_factory=list)


def apply_reviews(fragments: list[BankFragment], decisions_path: Path | None) -> list[BankFragment]:
    decisions = {} if decisions_path is None else {
        key: ReviewDecision.model_validate(value)
        for key, value in json.loads(decisions_path.read_text("utf-8")).items()
    }
    result = []
    for original in fragments:
        fragment = original.model_copy(deep=True, update={"review_status": "pending"})
        decision = decisions.get(fragment.fragment_id)
        if decision and decision.content_sha256 == fragment.content_sha256:
            fragment = fragment.model_copy(update=decision.model_dump(exclude={"content_sha256"}))
        result.append(fragment)
    return result
