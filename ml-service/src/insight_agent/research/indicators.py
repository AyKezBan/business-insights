"""Официальные показатели Банка России. Числа берутся из таблиц и XML и форматируются кодом, не моделью."""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from bs4 import BeautifulSoup

KEY_RATE_URL = "https://www.cbr.ru/hd_base/KeyRate/"
FX_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
FX_PAGE_URL = "https://www.cbr.ru/currency_base/daily/"

CURRENCY_NAMES = {
    "USD": "доллар США", "EUR": "евро", "CNY": "китайский юань", "TRY": "турецкая лира", "AED": "дирхам ОАЭ",
    "KZT": "казахстанский тенге", "BYN": "белорусский рубль", "INR": "индийская рупия", "JPY": "японская иена",
    "GBP": "фунт стерлингов", "CHF": "швейцарский франк", "UZS": "узбекский сум", "AMD": "армянский драм",
}


class ParseError(ValueError):
    pass


@dataclass
class OpenDocument:
    source_id: str
    title: str
    text: str
    url: str
    published: date | datetime
    retrieved_at: datetime
    kind: str  # indicator | press
    topics: list[str] = field(default_factory=list)


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.strip().replace("\xa0", "").replace(" ", "").replace(",", "."))
    except Exception as exc:  # noqa: BLE001 - любое нечисловое значение это ошибка источника
        raise ParseError(f"Не число: {raw!r}") from exc


def fmt(value: Decimal, places: int) -> str:
    """Русская запись числа: запятая, фиксированное число знаков, округление половина вверх."""
    q = Decimal(1).scaleb(-places)
    return f"{value.quantize(q, ROUND_HALF_UP):f}".replace(".", ",")


def fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _date(raw: str) -> date:
    try:
        return datetime.strptime(raw.strip(), "%d.%m.%Y").date()
    except ValueError as exc:
        raise ParseError(f"Не дата: {raw!r}") from exc


def _safe_xml(xml: bytes) -> ET.Element:
    upper = xml.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ParseError("DTD и сущности в XML запрещены")
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ParseError("Некорректный XML") from exc


def parse_key_rate(html: bytes | str) -> list[tuple[date, Decimal]]:
    text = html.decode("utf-8-sig", "replace") if isinstance(html, bytes) else html
    rows = []
    for tr in BeautifulSoup(text, "html.parser").find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) >= 2 and re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", cells[0]) and re.fullmatch(r"\d+[.,]?\d*", cells[1]):
            rows.append((_date(cells[0]), _decimal(cells[1])))
    if not rows:
        raise ParseError("В таблице ключевой ставки нет данных")
    return sorted(set(rows), reverse=True)


def parse_fx_daily(xml: bytes) -> tuple[date, dict[str, tuple[Decimal, str]]]:
    """Курс за 1 единицу валюты в рублях (VunitRate, либо Value/Nominal) и название валюты."""
    root = _safe_xml(xml)
    if root.tag != "ValCurs":
        raise ParseError("Ожидался ValCurs")
    effective = _date(root.get("Date", ""))
    rates = {}
    for valute in root.findall("Valute"):
        code = (valute.findtext("CharCode") or "").strip()
        if not code:
            continue
        if unit := valute.findtext("VunitRate"):
            per_unit = _decimal(unit)
        else:
            per_unit = _decimal(valute.findtext("Value") or "") / _decimal(valute.findtext("Nominal") or "1")
        rates[code] = (per_unit, (valute.findtext("Name") or "").strip())
    if not rates:
        raise ParseError("В XML нет курсов")
    return effective, rates


def key_rate_document(rows: list[tuple[date, Decimal]], as_of: date, retrieved_at: datetime) -> OpenDocument:
    visible = [r for r in rows if r[0] <= as_of]
    if not visible:
        raise ParseError("Нет значений ключевой ставки на дату среза")
    day, value = visible[0]
    run_start, previous = day, None
    for d, v in visible[1:]:
        if v == value:
            run_start = d
            continue
        previous = (d, v)
        break
    lines = [f"Ключевая ставка Банка России на {fmt_date(day)}: {fmt(value, 2)}% годовых."]
    if previous:
        delta = value - previous[1]
        lines.append(f"Значение действует с {fmt_date(run_start)}. Предыдущее значение: {fmt(previous[1], 2)}% годовых "
                     f"(на {fmt_date(previous[0])}). Изменение: {'+' if delta > 0 else ''}{fmt(delta, 2)} п.п.")
    else:
        lines.append(f"В таблице Банка России за период с {fmt_date(visible[-1][0])} по {fmt_date(day)} "
                     f"значение не менялось; дата последнего изменения ранее этого периода.")
    return OpenDocument(
        source_id=f"cbr_keyrate_{day.isoformat()}", title="Банк России: ключевая ставка", text="\n".join(lines),
        url=KEY_RATE_URL, published=day, retrieved_at=retrieved_at, kind="indicator", topics=["cost_of_debt", "deposit_yield"],
    )


def fx_document(code: str, now: tuple[date, dict], before: tuple[date, dict], retrieved_at: datetime) -> OpenDocument:
    (day, rates), (day0, rates0) = now, before
    if code not in rates or code not in rates0:
        raise ParseError(f"Нет курса {code}")
    value, source_name = rates[code]
    value0 = rates0[code][0]
    name = CURRENCY_NAMES.get(code) or source_name or code
    diff = value - value0
    pct = (value / value0 - 1) * 100 if value0 else Decimal(0)
    if diff == 0:
        move = "Курс за период не изменился."
    else:
        word = "вырос" if diff > 0 else "снизился"
        move = f"За период курс {word} на {fmt(abs(pct), 2)}% (на {fmt(abs(diff), 4)} руб.)."
    text = (f"Официальный курс Банка России на {fmt_date(day)}: 1 {name} ({code}) = {fmt(value, 4)} руб. "
            f"На {fmt_date(day0)}: {fmt(value0, 4)} руб. {move}")
    return OpenDocument(
        source_id=f"cbr_fx_{code.lower()}_{day.isoformat()}", title=f"Банк России: официальный курс {code}", text=text,
        url=FX_PAGE_URL, published=day, retrieved_at=retrieved_at, kind="indicator", topics=["fx", "import_costs", "export_demand"],
    )
