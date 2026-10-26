from datetime import UTC, date, datetime, timedelta

from insight_agent.schemas import NewsEvent, source_publication_time
from insight_agent.tools.bank import BankSearchArgs


def publication_day(event: NewsEvent) -> date | None:
    if event.published_date:
        return event.published_date
    if event.published_at:
        return source_publication_time(event.published_at,
            source_utc_offset_seconds=event.source.source_utc_offset_seconds,
            source_timezone=event.source.source_timezone).date()
    return None


def published_by(event: NewsEvent, cutoff: datetime) -> bool:
    cutoff = cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=UTC)
    if event.published_at:
        return event.published_at <= cutoff
    # A calendar day is not proof of an intraday publication time.
    day = publication_day(event)
    return day is not None and day < cutoff.date()


def bank_policy(event: NewsEvent, *, today: date, max_age_days: int = 180,
                cutoff: date | None = None) -> tuple[date, date]:
    if max_age_days < 1:
        raise ValueError("Bank evidence window must be positive")
    as_of = publication_day(event) or today
    if cutoff:
        as_of = min(as_of, cutoff)
    return as_of - timedelta(days=max_age_days), as_of


def restrict_bank_args(args: BankSearchArgs, bounds: tuple[date, date]) -> BankSearchArgs | None:
    lower, upper = bounds
    lower = max(lower, args.not_before) if args.not_before else lower
    upper = min(upper, args.not_after) if args.not_after else upper
    if lower > upper:
        return None
    return args.model_copy(update={"not_before": lower, "not_after": upper})
