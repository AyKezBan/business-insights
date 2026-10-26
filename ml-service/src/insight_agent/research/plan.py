"""Что именно искать в открытых источниках для конкретного клиента: решает код по профилю, а не модель."""
import re
from dataclasses import dataclass, field

from insight_agent.schemas import CompanyProfile

MAX_CURRENCIES = 4
MAX_KEYWORDS = 12

COUNTRY_CURRENCY = {
    "КИТАЙ": "CNY", "ТУРЦИЯ": "TRY", "США": "USD", "ГЕРМАНИЯ": "EUR", "ИТАЛИЯ": "EUR", "ФРАНЦИЯ": "EUR",
    "ИСПАНИЯ": "EUR", "НИДЕРЛАНДЫ": "EUR", "ФИНЛЯНДИЯ": "EUR", "АВСТРИЯ": "EUR", "ГРЕЦИЯ": "EUR",
    "ОАЭ": "AED", "АРАБСК": "AED", "КАЗАХСТАН": "KZT", "БЕЛАРУСЬ": "BYN", "ИНДИЯ": "INR", "ЯПОНИЯ": "JPY",
    "ВЕЛИКОБРИТАНИЯ": "GBP", "ШВЕЙЦАРИЯ": "CHF", "УЗБЕКИСТАН": "UZS", "АРМЕНИЯ": "AMD",
}
DEFAULT_CURRENCIES = ["USD", "EUR", "CNY"]

CHANNEL_STEMS = {
    "cost_of_debt": ["ключев", "ставк", "кредит"],
    "deposit_yield": ["ключев", "ставк", "депозит"],
    "liquidity": ["ликвидн", "ставк"],
    "fx": ["курс", "валют"],
    "export_demand": ["экспорт", "внешн"],
    "import_costs": ["импорт", "валют"],
    "domestic_demand": ["спрос", "потреблен", "розничн"],
    "input_prices": ["цен", "инфляц", "топлив"],
    "commodity_prices": ["нефт", "золот", "сырь"],
    "labor_costs": ["зарплат", "труд", "занятост"],
    "regulation": ["регулир", "норматив", "требован"],
    "tax": ["налог", "бюджет"],
    "counterparty_risk": ["рейтинг", "банкрот"],
    "sanctions": ["санкц", "ограничен"],
}
_STOP = {"прочее", "прочих", "включая", "деятельность", "производство", "предоставление", "услуг", "области", "связанных"}


@dataclass
class ResearchPlan:
    key_rate: bool = False
    currencies: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.key_rate or self.currencies or self.keywords)


def currencies_for(countries: list[str]) -> list[str]:
    found: list[str] = []
    for country in countries:
        name = country.strip().upper()
        for marker, code in COUNTRY_CURRENCY.items():
            if marker in name and code not in found:
                found.append(code)
    return found


def build_plan(profile: CompanyProfile, matched_channels: list[str], extra: str | None = None) -> ResearchPlan:
    plan = ResearchPlan()
    channels = set(matched_channels)
    products = profile.products
    financing = [n for n in ("loans", "leasing", "deposits", "factoring") if getattr(products, n) is True]
    if financing or channels & {"cost_of_debt", "deposit_yield", "liquidity"}:
        plan.key_rate = True
        plan.reasons.append("ключевая ставка: " + (", ".join(f"products.{n}" for n in financing) or "каналы " + ", ".join(sorted(channels))))
    if profile.foreign_trade is True or "fx" in channels:
        codes = currencies_for(profile.foreign_trade_countries) or DEFAULT_CURRENCIES
        plan.currencies = codes[:MAX_CURRENCIES]
        plan.reasons.append("курсы валют: " + ("foreign_trade" if profile.foreign_trade else "канал fx") + f" -> {', '.join(plan.currencies)}")
    stems: list[str] = []
    for channel in matched_channels:
        stems += CHANNEL_STEMS.get(channel, [])
    if plan.key_rate:
        stems += ["ключев", "ставк"]
    if plan.currencies:
        stems += ["курс", "валют"]
    for token in re.findall(r"[а-яё]{5,}", (profile.okved_name or "").lower()):
        if token not in _STOP:
            stems.append(token[:6])
    if extra:
        stems += [t[:6] for t in re.findall(r"[а-яё]{4,}", extra.lower())]
    plan.keywords = list(dict.fromkeys(stems))[:MAX_KEYWORDS]
    if plan.keywords:
        plan.reasons.append("пресс-релизы по темам: " + ", ".join(plan.keywords))
    return plan
