from datetime import date, timedelta

from insight_agent.schemas import NewsEvent
from insight_agent.tools.bank import BankSearchArgs


def bank_policy(event: NewsEvent, *, today: date, max_age_days: int = 180) -> tuple[date, date]:
    if max_age_days < 1:
        raise ValueError("Bank evidence window must be positive")
    as_of = event.published_at.date() if event.published_at is not None else today
    return as_of - timedelta(days=max_age_days), as_of


def restrict_bank_args(args: BankSearchArgs, bounds: tuple[date, date]) -> BankSearchArgs | None:
    lower, upper = bounds
    lower = max(lower, args.not_before) if args.not_before else lower
    upper = min(upper, args.not_after) if args.not_after else upper
    if lower > upper:
        return None
    return args.model_copy(update={"not_before": lower, "not_after": upper})
