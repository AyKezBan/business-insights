from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel

from insight_agent.schemas import CompanyProfile

CHANNELS = {
    "cost_of_debt": "стоимость заёмного финансирования",
    "deposit_yield": "доходность размещения свободных средств",
    "liquidity": "ликвидность и оборотный капитал",
    "fx": "валютный курс",
    "export_demand": "внешний спрос и экспортные цены",
    "import_costs": "стоимость импорта",
    "domestic_demand": "внутренний спрос",
    "input_prices": "цены на сырьё, материалы, топливо",
    "commodity_prices": "цены на биржевые товары",
    "labor_costs": "затраты на персонал и рынок труда",
    "regulation": "регулирование и тарифы",
    "tax": "налоги и бюджет",
    "counterparty_risk": "надёжность и концентрация контрагентов",
    "sanctions": "санкции и ограничения внешней торговли",
}

OKVED_CHANNELS: list[tuple[range, list[str]]] = [
    (range(1, 4), ["commodity_prices", "input_prices", "fx", "export_demand"]),
    (range(5, 10), ["commodity_prices", "fx", "export_demand", "sanctions", "tax"]),
    (range(10, 13), ["input_prices", "domestic_demand", "import_costs"]),
    (range(13, 24), ["input_prices", "domestic_demand", "import_costs", "fx"]),
    (range(24, 26), ["commodity_prices", "export_demand", "input_prices", "fx"]),
    (range(26, 34), ["input_prices", "import_costs", "fx", "domestic_demand"]),
    (range(35, 40), ["regulation", "input_prices", "cost_of_debt"]),
    (range(41, 44), ["cost_of_debt", "domestic_demand", "input_prices", "regulation"]),
    (range(45, 47), ["domestic_demand", "import_costs", "fx", "liquidity"]),
    (range(47, 48), ["domestic_demand", "labor_costs", "import_costs"]),
    (range(49, 54), ["input_prices", "regulation", "domestic_demand"]),
    (range(55, 57), ["domestic_demand", "labor_costs"]),
    (range(58, 64), ["labor_costs", "regulation", "import_costs"]),
    (range(64, 67), ["cost_of_debt", "deposit_yield", "regulation"]),
    (range(68, 69), ["cost_of_debt", "domestic_demand"]),
    (range(69, 83), ["labor_costs", "domestic_demand"]),
]

LARGE_BUSINESS_RUB = Decimal(15_000_000_000)
REVENUE_PLACEHOLDER_RUB = Decimal(1000)
BILLION = Decimal(10) ** 9
MILLION = Decimal(10) ** 6
TENTH = Decimal("0.1")


class Signal(BaseModel):
    feature: str
    value: str
    channels: list[str]
    note: str


def _okved_channels(code: str | None) -> list[str]:
    if not code:
        return []
    try:
        prefix = int(code.split(".")[0])
    except ValueError:
        return []
    for rng, channels in OKVED_CHANNELS:
        if prefix in rng:
            return channels
    return []


def _known_revenue(value: Decimal | None) -> Decimal | None:
    if value is None or value == REVENUE_PLACEHOLDER_RUB or value < 0:
        return None
    return value


def _format_rub(value: Decimal) -> str:
    for unit, suffix in ((BILLION, "млрд"), (MILLION, "млн")):
        rounded = (value / unit).quantize(TENTH, ROUND_HALF_UP)
        if rounded >= 1:
            return f"{rounded} {suffix} руб."
    return f"{value.quantize(Decimal(1), ROUND_HALF_UP):,} руб.".replace(",", " ")


def derive_signals(p: CompanyProfile) -> list[Signal]:
    signals: list[Signal] = []
    if p.okved_code:
        signals.append(
            Signal(
                feature="okved_code",
                value=f"{p.okved_code} {p.okved_name or ''}".strip(),
                channels=_okved_channels(p.okved_code),
                note="основной вид деятельности",
            )
        )
    group_revenue, revenue = _known_revenue(p.group_revenue_rub), _known_revenue(p.revenue_rub)
    if group_revenue is not None or revenue is not None:
        feature = "group_revenue_rub" if group_revenue is not None else "revenue_rub"
        value = group_revenue if group_revenue is not None else revenue
        scope = "группа" if group_revenue is not None else (p.revenue_scope or "unknown")
        note = f"выручка; год: {p.revenue_year or 'неизвестен'}; scope: {scope}; сегмент не установлен"
        signals.append(Signal(feature=feature, value=_format_rub(value), channels=[], note=note))
    product_channels = {
        "loans": (["cost_of_debt", "liquidity"], "кредиты в банке: чувствительность к ставкам"),
        "leasing": (["cost_of_debt"], "лизинг: стоимость фондирования основных средств"),
        "deposits": (["deposit_yield", "liquidity"], "депозиты: доходность свободных средств"),
        "factoring": (["liquidity", "counterparty_risk"], "факторинг: зависимость от оборачиваемости дебиторки"),
        "guarantees": (["counterparty_risk", "regulation"], "гарантии: контрактные обязательства, госзаказ"),
        "payroll": (["labor_costs"], "зарплатный проект: фонд оплаты труда"),
        "settlement": ([], "расчётный счёт в банке"),
    }
    for name, (channels, note) in product_channels.items():
        value = getattr(p.products, name)
        if value is True:
            signals.append(Signal(feature=f"products.{name}", value="да", channels=channels, note=note))
    if p.foreign_trade:
        countries = ", ".join(p.foreign_trade_countries) or "страны не указаны"
        signals.append(
            Signal(
                feature="foreign_trade",
                value=countries,
                # В данных ФТС нет направления сделок: импорт или экспорт из них не следует.
                channels=["fx", "sanctions"],
                note="компания есть в данных ФТС; страны не означают импорт или экспорт, направление и объём не установлены",
            )
        )
    # переводы себе - не контрагент; индекс в признаке остаётся исходным (Backend сверяет counterparties[i] с отправленным снимком)
    external = [(i, cp) for i, cp in enumerate(p.counterparties) if cp.company_id != p.company_id]
    self_note = f"; доля переводов между своими счетами {p.self_transfer_share:.0%}, зависимостью не считается" if p.self_transfer_share is not None else ""
    for i, cp in sorted(external, key=lambda item: item[1].share_of_flow or 0, reverse=True)[:5]:
        share = cp.share_of_flow
        channels = ["counterparty_risk"] + (_okved_channels(cp.okved_code)[:2] if cp.direction == "outgoing" else [])
        role = "входящий платёжный контрагент" if cp.direction == "incoming" else "исходящий платёжный контрагент"
        signals.append(
            Signal(
                feature=f"counterparties[{i}]",
                value=f"{cp.company_id} ({role}, {cp.okved_name or 'отрасль неизвестна'}"
                + (f", {share:.0%} потока" if share is not None else "")
                + ")",
                channels=channels,
                note="крупный контрагент по платежам; характер отношений не подтверждён" + self_note,
            )
        )
    if p.group_member_okveds:
        channels = sorted({c for code in p.group_member_okveds for c in _okved_channels(code)})
        signals.append(
            Signal(
                feature="group_member_okveds",
                value=", ".join(p.group_member_okveds),
                channels=channels,
                note="виды деятельности других компаний группы; роль каждой компании и связь с этой не установлены, вывод по группе не переносится на компанию без проверки",
            )
        )
    if p.region:
        signals.append(Signal(feature="region", value=p.region, channels=[], note="регион регистрации"))
    for key, value in p.user_provided.items():
        signals.append(Signal(feature=f"user_provided.{key}", value=value, channels=[], note="со слов пользователя"))
    return signals


def missing_profile_data(p: CompanyProfile) -> list[str]:
    missing = list(p.missing_fields)
    if p.products.loans is None:
        missing.append("products.loans")
    if not p.okved_code:
        missing.append("okved_code")
    if _known_revenue(p.revenue_rub) is None and _known_revenue(p.group_revenue_rub) is None:
        missing.append("revenue_rub")
    if p.foreign_trade is None:
        missing.append("foreign_trade")
    return sorted(set(missing))
