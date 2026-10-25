from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from insight_agent.research.fetch import OpenSourceError, SafeFetcher
from insight_agent.research.indicators import ParseError, fx_document, key_rate_document, parse_fx_daily, parse_key_rate
from insight_agent.research.plan import build_plan, currencies_for
from insight_agent.research.service import OpenResearch, TtlCache
from insight_agent.schemas import CompanyProfile, Products

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def fx_xml(effective: str, usd: str, eur: str = "92,1000", cny: str = "11,5000") -> bytes:
    rows = "".join(
        f'<Valute ID="R{i}"><NumCode>0</NumCode><CharCode>{c}</CharCode><Nominal>1</Nominal><Name>{n}</Name>'
        f"<Value>{v}</Value><VunitRate>{v}</VunitRate></Valute>"
        for i, (c, n, v) in enumerate([("USD", "Доллар США", usd), ("EUR", "Евро", eur), ("CNY", "Юань", cny)])
    )
    return f'<?xml version="1.0" encoding="windows-1251"?><ValCurs Date="{effective}" name="x">{rows}</ValCurs>'.encode("cp1251")


def key_rate_html(rows: list[tuple[str, str]]) -> bytes:
    body = "".join(f"<tr><td>{d}</td><td>{v}</td></tr>" for d, v in rows)
    return f"<html><body><table><tr><th>Дата</th><th>Ставка</th></tr>{body}</table></body></html>".encode()


RATE_ROWS = [("23.10.2026", "14,00"), ("22.10.2026", "14,00"), ("11.09.2026", "14,00"), ("10.09.2026", "15,00"), ("09.09.2026", "15,00")]
RSS = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>
<item><title>Банк России сохранил ключевую ставку на уровне 14,00% годовых</title><link>https://www.cbr.ru/press/pr/1/</link><pubDate>Fri, 11 Sep 2026 13:30:00 +0300</pubDate></item>
<item><title>Банк России расширил требования к кредитным организациям</title><link>https://www.cbr.ru/press/pr/2/</link><pubDate>Fri, 24 Oct 2026 10:00:00 +0300</pubDate></item>
<item><title>Конкурс детского рисунка</title><link>https://www.cbr.ru/press/pr/3/</link><pubDate>Fri, 23 Oct 2026 10:00:00 +0300</pubDate></item>
</channel></rss>"""


class Site:
    """Фейковый cbr.ru: считает обращения, чтобы проверять кеш."""

    def __init__(self, fail_key_rate=False):
        self.requests: list[str] = []
        self.fail_key_rate = fail_key_rate

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        path = request.url.path
        if path == "/hd_base/KeyRate/":
            return httpx.Response(500) if self.fail_key_rate else httpx.Response(200, content=key_rate_html(RATE_ROWS))
        if path == "/scripts/XML_daily.asp":
            old = request.url.params["date_req"].startswith(("25/09", "10/09"))
            return httpx.Response(200, content=fx_xml("25.09.2026", "80,0000") if old else fx_xml("24.10.2026", "83,5588"))
        if path == "/rss/RssPress":
            return httpx.Response(200, content=RSS.encode())
        if path.startswith("/press/pr/"):
            return httpx.Response(200, content=f'<html><body><div class="landing-text">Текст релиза {path}. Ставка 14,00%.</div></body></html>'.encode())
        return httpx.Response(404)


def research(site=None, cache=None) -> tuple[OpenResearch, Site]:
    site = site or Site()
    client = httpx.Client(transport=httpx.MockTransport(site))
    return OpenResearch(SafeFetcher(client), cache or TtlCache(clock=lambda: NOW), clock=lambda: NOW), site


def test_key_rate_text_has_exact_values_dates_and_change():
    doc = key_rate_document(parse_key_rate(key_rate_html(RATE_ROWS)), date(2026, 10, 25), NOW)
    assert "на 23.10.2026: 14,00% годовых" in doc.text
    assert "Значение действует с 11.09.2026" in doc.text
    assert "Предыдущее значение: 15,00% годовых (на 10.09.2026)" in doc.text
    assert "Изменение: -1,00 п.п." in doc.text


def test_key_rate_respects_the_time_slice():
    doc = key_rate_document(parse_key_rate(key_rate_html(RATE_ROWS)), date(2026, 9, 10), NOW)
    assert "на 10.09.2026: 15,00%" in doc.text and "14,00" not in doc.text
    with pytest.raises(ParseError):
        key_rate_document(parse_key_rate(key_rate_html(RATE_ROWS)), date(2026, 1, 1), NOW)


def test_key_rate_without_a_change_in_window_does_not_invent_a_date():
    doc = key_rate_document(parse_key_rate(key_rate_html(RATE_ROWS[:3])), date(2026, 10, 25), NOW)
    assert "Предыдущее значение" not in doc.text and "не менялось" in doc.text


def test_fx_document_computes_change_with_decimal_rounding():
    doc = fx_document("USD", parse_fx_daily(fx_xml("24.10.2026", "83,5588")), parse_fx_daily(fx_xml("25.09.2026", "80,0000")), NOW)
    assert "1 доллар США (USD) = 83,5588 руб." in doc.text
    assert "На 25.09.2026: 80,0000 руб." in doc.text
    assert "вырос на 4,45% (на 3,5588 руб.)" in doc.text


def test_fx_unknown_currency_is_not_invented():
    now, before = parse_fx_daily(fx_xml("24.10.2026", "83,5588")), parse_fx_daily(fx_xml("25.09.2026", "80,0000"))
    with pytest.raises(ParseError):
        fx_document("TRY", now, before, NOW)


def test_xml_with_entities_is_rejected():
    with pytest.raises(ParseError):
        parse_fx_daily(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><ValCurs Date="24.10.2026"/>')


@pytest.mark.parametrize("url", ["http://www.cbr.ru/rss/RssPress", "https://evil.example/rss", "https://www.cbr.ru:8443/x",
                                 "https://user:pw@www.cbr.ru/x"])
def test_fetcher_refuses_urls_outside_the_allowlist(url):
    fetcher = SafeFetcher(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x"))))
    with pytest.raises(OpenSourceError):
        fetcher.get(url)


def test_fetcher_refuses_redirect_to_another_host_and_big_bodies():
    redirect = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "https://127.0.0.1/admin"})))
    with pytest.raises(OpenSourceError):
        SafeFetcher(redirect).get("https://www.cbr.ru/a")
    big = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 100)))
    with pytest.raises(OpenSourceError):
        SafeFetcher(big, max_bytes=10).get("https://www.cbr.ru/a")


def test_plan_follows_the_clients_profile():
    loans = CompanyProfile(company_id="U_1", products=Products(loans=True))
    plan = build_plan(loans, ["cost_of_debt"])
    assert plan.key_rate and plan.currencies == [] and "ключев" in plan.keywords

    trader = CompanyProfile(company_id="U_2", foreign_trade=True, foreign_trade_countries=["КИТАЙ", "ТУРЦИЯ", "КИТАЙ"])
    assert build_plan(trader, []).currencies == ["CNY", "TRY"]
    assert build_plan(CompanyProfile(company_id="U_3", foreign_trade=True), []).currencies == ["USD", "EUR", "CNY"]


def test_plan_is_empty_when_nothing_in_the_profile_calls_for_research():
    plan = build_plan(CompanyProfile(company_id="U_4", products=Products(loans=False)), [])
    assert plan.empty and not plan.reasons


def test_country_names_map_to_currencies():
    assert currencies_for(["ГЕРМАНИЯ", "Италия", "ОБЪЕДИНЕННЫЕ АРАБСКИЕ ЭМИРАТЫ", "Атлантида"]) == ["EUR", "AED"]


def test_search_returns_indicators_and_relevant_press_up_to_the_slice():
    service, site = research()
    plan = build_plan(CompanyProfile(company_id="U_1", products=Products(loans=True), foreign_trade=True,
                                     foreign_trade_countries=["КИТАЙ"]), ["cost_of_debt"])
    found = service.search(plan, as_of=date(2026, 10, 25), top_k=3)

    ids = {d.source_id for d in found.docs}
    assert {"cbr_keyrate_2026-10-23", "cbr_fx_cny_2026-10-24"} <= ids
    press = [d for d in found.docs if d.kind == "press"]
    assert press and "Конкурс" not in " ".join(d.title for d in press)
    assert found.errors == []

    earlier = service.search(plan, as_of=date(2026, 9, 12), top_k=3)
    assert [d.title for d in earlier.docs if d.kind == "press"] == ["Банк России сохранил ключевую ставку на уровне 14,00% годовых"]


def test_second_search_is_served_from_cache():
    service, site = research()
    plan = build_plan(CompanyProfile(company_id="U_1", products=Products(loans=True)), ["cost_of_debt"])
    service.search(plan, as_of=date(2026, 10, 25))
    made = len(site.requests)
    service.search(plan, as_of=date(2026, 10, 25))
    assert made > 0 and len(site.requests) == made


def test_one_failing_source_does_not_hide_the_others():
    service, _ = research(Site(fail_key_rate=True))
    plan = build_plan(CompanyProfile(company_id="U_1", products=Products(loans=True), foreign_trade=True), ["cost_of_debt"])
    found = service.search(plan, as_of=date(2026, 10, 25))
    assert any(d.kind == "indicator" and "fx" in d.source_id for d in found.docs)
    assert len(found.errors) == 1 and found.errors[0].startswith("ключевая ставка")


def test_empty_plan_makes_no_requests():
    service, site = research()
    assert service.search(build_plan(CompanyProfile(company_id="U_4"), []), as_of=date(2026, 10, 25)).docs == []
    assert site.requests == []


def test_keyword_must_match_the_start_of_a_word():
    service, _ = research()
    plan = build_plan(CompanyProfile(company_id="U_1", foreign_trade=True), [])
    assert "курс" in plan.keywords
    titles = [d.title for d in service.search(plan, as_of=date(2026, 10, 25)).docs if d.kind == "press"]
    assert all("Конкурс" not in t for t in titles)
