"""D09/D10 frontend contract stub. Python 3.11+, no third party packages."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import argparse
import html
import json
import time
import uuid

ROOT = Path(__file__).resolve().parent
COMPANIES = [
    {"id": "retail", "name": "Учебная розница", "sector": "Торговля одеждой"},
    {"id": "energy", "name": "Учебная энергетика", "sector": "Электроэнергетика"},
    {"id": "empty", "name": "Новая компания", "sector": "Профиль ещё не заполнен"},
]
ROLES = {"cfo": "Финансовый директор", "ceo": "Генеральный директор"}
VIEWS = {"overview": "Общий обзор", "consequences": "Последствия", "risks": "Риски", "recommendations": "Рекомендации"}
DEFAULT_PROFILE = {"name": "Алексей", "company_id": "retail", "role": "cfo"}
NOTICE = "Учебный прототип D09/D10. Компании, события и источники синтетические. Ответы подготовлены для проверки интерфейса; LLM не подключена."
SOURCES = {
    "news-rate": {"id": "news-rate", "title": "Учебная новость: ставка без изменений", "date": "2026-09-11", "page": 1,
                  "text": "В учебном сценарии ставка сохранена без изменения. Условия кредитов конкретных компаний в новости не приведены."},
    "report-rate": {"id": "report-rate", "title": "Учебная аналитика: стоимость финансирования", "date": "2026-09-12", "page": 2,
                    "text": "В синтетическом сценарии быстрое удешевление нового финансирования не гарантировано. Для действующих кредитов эффект зависит от типа ставки и договора. Это не позиция Альфа-Банка."},
    "news-route": {"id": "news-route", "title": "Учебная новость: задержки перевозок", "date": "2026-09-12", "page": 1,
                   "text": "В учебном сценарии возникли задержки на транспортном маршруте. Список затронутых клиентов не опубликован."},
}
EVENTS = {
    "rate": {"id": "rate", "title": "Пауза в снижении ставки", "category": "Финансирование", "date": "2026-09-11"},
    "route": {"id": "route", "title": "Задержки на транспортном маршруте", "category": "Логистика", "date": "2026-09-12"},
}


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def context(data, event=False, view=False):
    keys = ["company_id", "role"] + (["event_id"] if event else []) + (["view"] if view else [])
    ctx = {key: data.get(key) for key in keys}
    if ctx["company_id"] not in [c["id"] for c in COMPANIES] or ctx["role"] not in ROLES:
        raise APIError("Неизвестная компания или роль.")
    if event and ctx["event_id"] not in EVENTS:
        raise APIError("Карточка не найдена.", 404)
    if view and ctx["view"] not in VIEWS:
        raise APIError("Неизвестный разрез.")
    return ctx


def feed(data):
    ctx = context(data)
    items = []
    if ctx["company_id"] != "empty":
        for event in EVENTS.values():
            why = ("Проверьте планы нового финансирования и сроки договоров." if ctx["role"] == "cfo"
                   else "Сопоставьте условия финансирования с планами развития бизнеса.")
            if event["id"] == "route":
                why = "Возможная связь требует проверки: маршруты поставок компании неизвестны."
            items.append({**event, "why": why, "status": "limited"})
    return {"context": ctx, "items": items, "as_of": "2026-09-14"}


def insight(data):
    ctx = context(data, event=True, view=True)
    if ctx["company_id"] == "empty":
        raise APIError("Для этой компании пока нет подготовленной аналитики.", 404)
    rate = ctx["event_id"] == "rate"
    news_id = "news-rate" if rate else "news-route"
    source_ids = [news_id, "report-rate"] if rate else [news_id]
    impact = ("Для розницы: замедление оборота запасов может увеличить потребность в оборотных средствах."
              if ctx["company_id"] == "retail" else
              "Для энергетики: стоимость нового финансирования может влиять на экономику инвестиционных проектов.")
    recommendation = ("Проверьте сроки, тип ставки по кредитам и потребность в новом финансировании."
                      if ctx["role"] == "cfo" else "Проверьте устойчивость инвестиционных планов при разных условиях финансирования.")
    # Structured fixture: type/origin and supporting sources stay explicit in every view.
    def item(kind, text, ids, certainty="hypothesis"):
        return {"kind": kind, "text": text, "source_ids": ids, "certainty": certainty}

    public = [
        item("consequences", "Сохранение ставки само по себе не меняет условия договоров компаний. Возможное влияние на финансирование нужно оценивать отдельно." if rate else "Задержки могут сдвинуть сроки поставок участников маршрута. Какие компании затронуты, пока неизвестно.", [news_id], "inference"),
        item("risks", "Риск — рассчитывать на снижение стоимости финансирования без подтверждённых новых условий." if rate else "Возможны нарушения сроков доставки, если поставки компании проходят по этому маршруту.", [news_id]),
        item("recommendations", "Сопоставьте новость с условиями финансирования и планами компании." if rate else "Проверьте используемые маршруты и сообщения перевозчиков о сроках.", [news_id], "recommendation"),
    ]
    bank = [
        item("consequences", "Быстрое удешевление нового финансирования не гарантировано; влияние на действующие кредиты зависит от договора.", ["report-rate"], "assessment"),
        item("risks", "Условия действующего кредита могут отличаться от общих рыночных ожиданий. Без договора нельзя переносить оценку на конкретное обязательство.", ["report-rate"], "assessment"),
        item("recommendations", "Проверьте тип ставки и условия договора, прежде чем оценивать изменение расходов.", ["report-rate"], "recommendation"),
    ] if rate else [item(kind, "Релевантного материала банка по этой теме в доступном наборе нет.", [], "unavailable") for kind in ("consequences", "risks", "recommendations")]
    company = [
        item("consequences", impact if rate else "Без данных о маршрутах нельзя подтвердить влияние на эту компанию.", source_ids),
        item("risks", ("При замедлении продаж возможен рост потребности в оборотных средствах. Данные о запасах и договорах отсутствуют." if ctx["company_id"] == "retail" else "Новые условия финансирования могут изменить сроки окупаемости проектов. Их бюджеты и договоры неизвестны.") if rate else "Риск для компании остаётся гипотезой: связь её поставок с маршрутом не подтверждена.", source_ids),
        item("recommendations", recommendation if rate else "Уточните у логистики маршруты и сроки ближайших поставок.", source_ids, "recommendation"),
    ]
    blocks = [{"kind": "fact", "text": SOURCES[news_id]["text"], "source_ids": [news_id], "certainty": "fact"}]
    for kind, sections in (("open", public), ("bank", bank), ("ai", company)):
        selected = sections if ctx["view"] == "overview" else [s for s in sections if s["kind"] == ctx["view"]]
        blocks.append({"kind": kind, "sections": selected, "source_ids": sorted({sid for s in selected for sid in s["source_ids"]}), "synthetic": True})
    if ctx["view"] != "overview":
        blocks = [b for b in blocks if b["kind"] != "fact"]
    return {"context": ctx, "insight_id": f"{ctx['company_id']}:{ctx['event_id']}:v2",
            "title": EVENTS[ctx["event_id"]]["title"], "status": "limited", "blocks": blocks,
            "disclaimer": "Аналитика не является инвестиционной рекомендацией.",
            "missing_data": ["Неизвестны суммы и условия кредитов; денежный эффект не рассчитан."] if rate else ["Не подтверждена связь компании с маршрутом."],
            "sources": [{k: v for k, v in SOURCES[s].items() if k != "text"} for s in source_ids]}


def clarify(data):
    expected = {"company_id", "role", "event_id", "view", "insight_id", "question", "request_id"}
    if not isinstance(data, dict) or set(data) != expected:
        raise APIError("Поля запроса не соответствуют контракту.")
    for key in expected:
        if not isinstance(data[key], str):
            raise APIError("Все поля запроса должны быть строками.")
    if not 1 <= len(data["request_id"]) <= 100:
        raise APIError("Некорректный request_id.")
    card = insight(data)
    if card["insight_id"] != data["insight_id"]:
        raise APIError("Версия карточки изменилась. Откройте её заново.", 409)
    q = data["question"].strip()
    if not 1 <= len(q) <= 1000:
        raise APIError("Вопрос должен содержать от 1 до 1000 символов.")
    lower = q.lower()
    if any(word in lower for word in ("провер", "рекоменд", "последств", "риск", "основан", "источник", "важно", "почему")):
        section_kind = "recommendations" if any(w in lower for w in ("провер", "рекоменд")) else "risks" if "риск" in lower else "consequences"
        company = next(b for b in insight({**data, "view": "overview"})["blocks"] if b["kind"] == "ai")
        answer = next(s["text"] for s in company["sections"] if s["kind"] == section_kind)
        status = "limited"
    elif any(word in lower for word in ("сколько", "рассчит", "сумм", "долг")):
        answer = "Денежный эффект определить нельзя: нет подтверждённых сумм, ставок и сроков обязательств. Уточните эти данные."
        status = "no_answer"
    else:
        answer = "В учебном API нет подготовленного ответа на этот вопрос. Можно спросить, почему событие важно или что проверить. В рабочем решении вопрос обрабатывает D11."
        status = "no_answer"
    return {"context": card["context"], "insight_id": card["insight_id"], "request_id": data["request_id"],
            "answer_id": str(uuid.uuid4()), "status": status, "text": answer,
            "sources": card["sources"] if status != "no_answer" else [], "missing_data": card["missing_data"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send(self, value, status=200, mime="application/json; charset=utf-8"):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        for key, val in {"Content-Type": mime, "Content-Length": str(len(body)), "Cache-Control": "no-store",
                         "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                         "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"}.items():
            self.send_header(key, val)
        self.end_headers()
        try:
            self.wfile.write(body)
        except ConnectionError:
            pass

    def do_GET(self):
        url = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                     "/integration.js": ("integration.js", "text/javascript"),
                     "/backend-bindings.js": ("backend-bindings.js", "text/javascript"),
                     "/styles.css": ("styles.css", "text/css")}
            if url.path == "/config.js":
                return self.send(b"window.INSIGHTS_CONFIG = {mode: 'demo'};", mime="text/javascript; charset=utf-8")
            if url.path in files:
                name, mime = files[url.path]
                return self.send((ROOT / "web" / name).read_bytes(), mime=mime + "; charset=utf-8")
            if url.path == "/api/bootstrap":
                return self.send({"schema_version": "2.0", "notice": NOTICE, "companies": COMPANIES, "roles": ROLES, "views": VIEWS, "profile": DEFAULT_PROFILE})
            if url.path in ("/api/feed", "/api/insight"):
                time.sleep(self.server.demo_delay)
                return self.send(feed(params) if url.path == "/api/feed" else insight(params))
            if url.path.startswith("/sources/"):
                source = SOURCES.get(url.path.removeprefix("/sources/"))
                if not source:
                    raise APIError("Источник не найден.", 404)
                text = ("<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                        "<link rel='stylesheet' href='/styles.css'><title>Учебный источник</title></head><body><main class='source-page'>"
                        "<p class='eyebrow'>Синтетический первоисточник</p><h1>" + html.escape(source["title"]) + "</h1><p>" +
                        html.escape(source["date"]) + " · страница " + str(source["page"]) + "</p><article>" + html.escape(source["text"]) +
                        "</article><p>Не является позицией Альфа-Банка.</p><p>Закройте эту вкладку, чтобы вернуться к карточке в сохранённом контексте.</p></main></body></html>")
                return self.send(text.encode("utf-8"), mime="text/html; charset=utf-8")
            raise APIError("Страница не найдена.", 404)
        except APIError as exc:
            self.send({"error": {"message": str(exc)}}, exc.status)

    def do_POST(self):
        try:
            if self.path != "/api/clarifications":
                raise APIError("Не найдено.", 404)
            if self.headers.get_content_type() != "application/json":
                raise APIError("Ожидается application/json.", 415)
            if self.headers.get("X-Demo-Client") != "d09-d10":
                raise APIError("Отсутствует заголовок клиента.", 403)
            if self.headers.get("Origin") not in (None, "http://" + self.headers.get("Host", "")):
                raise APIError("Другой origin не разрешён.", 403)
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise APIError("Некорректная длина тела.") from None
            if not 0 < size <= 8192:
                raise APIError("Превышен размер запроса.", 413)
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            time.sleep(self.server.demo_delay)
            return self.send(clarify(data))
        except (ValueError, UnicodeError, TypeError):
            self.send({"error": {"message": "Некорректный JSON или тип поля."}}, 400)
        except APIError as exc:
            self.send({"error": {"message": str(exc)}}, exc.status)


def make_server(host="127.0.0.1", port=8766, delay=0.25):
    server = ThreadingHTTPServer((host, port), Handler)
    server.demo_delay = delay
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--delay", type=float, default=0.25, help="Учебная задержка API для проверки состояний UI")
    args = parser.parse_args()
    server = make_server(args.host, args.port, max(0, min(args.delay, 10)))
    print(f"D09/D10: http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
