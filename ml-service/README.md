# ML Service

Python-сервис для сбора и анализа новостей и банковской аналитики, оценки релевантности и формирования персонализированных инсайтов.

Получает от Backend API готовый профиль компании и контекст задачи. Возвращает результаты анализа и ответы на уточняющие вопросы.

Как устроен агент - в [docs/insight-agent.md](docs/insight-agent.md).

## Запуск

```bash
cd ml-service
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
cp .env.example .env    # LLM_BASE_URL и LLM_API_KEY
.venv/bin/uvicorn insight_agent.main:app --port 8000
```

Docker:

```bash
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t insight-agent ml-service
docker run --rm -p 8000:8000 --env-file ml-service/.env \
  -v "$PWD/ml-service/data:/app/data:ro" -v insight-agent-traces:/app/traces insight-agent
curl localhost:8000/health
```

Образ собирается на Python 3.12 с OCR (tesseract, poppler); локально подходит Python 3.11 и новее, тесты идут на обоих. Том `traces` хранит трассы, готовые инсайты и суточный счётчик рублей, его стоит сохранять между перезапусками.

Один запрос без сервера (без ключа LLM вернётся `status: failed`):

```bash
.venv/bin/insight-agent tests/fixtures/request.json
```

## API

- `GET /health`
- `POST /v1/insights` - инсайт по событию; необязательные `request_id` (повтор с тем же содержимым отдаёт готовый результат, другое содержимое - `409`) и `as_of` (срез по времени)
- `POST /v1/insights/{insight_id}/questions` - уточняющий вопрос по карточке
- `GET /v1/runs/{run_id}/trace` - трасса запуска

Схемы запросов и ответов лежат в [contracts/ml](../contracts/ml). Если поменяли `schemas.py`, их надо перевыгрузить:

```bash
.venv/bin/python -m insight_agent.export_schemas
```

## Данные

- `BANK_FRAGMENTS_PATH` (по умолчанию `data/bank_fragments.jsonl`) - фрагменты аналитики банка
- `NEWS_PATH` (`data/news.jsonl`) - новости
- `TRACE_DIR` (`traces`) - трассы (один файл на запуск), `insights/` с готовыми карточками, `budget_state.json` с суточным счётчиком рублей
- поиск по открытым официальным источникам (`OPEN_RESEARCH_ENABLED`, по умолчанию включён): ключевая ставка, курсы валют и пресс-релизы Банка России выбираются по профилю клиента, кеш в `TRACE_DIR/open_cache`; для работы нужен выход в интернет к `cbr.ru`, `/health` показывает `open_research`
- бюджет LLM в рублях (`LLM_DAILY_BUDGET_RUB`, пороги, цены моделей `LLM_PRICES_JSON`) и предел времени запуска `AGENT_MAX_SECONDS`: см. `.env.example` и [docs/insight-agent.md](docs/insight-agent.md). **Тарифы провайдера не подтверждены**, цены по умолчанию завышены

`data/` и `traces/` в гит не коммитим. В `tests/fixtures` только выдуманные данные.

## Тесты

```bash
.venv/bin/pytest
```

Вместо LLM в тестах подставляется фейковый клиент (`tests/fakes.py`), ключ не нужен. Соответствие тестов сценариям T01-T20 - в [docs/insight-agent.md](docs/insight-agent.md).

## Проверка на новых событиях (D05)

Раннер читает манифест пар «событие + профиль» (строка JSONL = `ManifestRow`, см. `src/insight_agent/evaluation.py`), прогоняет агента, механически проверяет исход, признаки и запрещённые фразы, считает M7 и M8 и пишет отчёт со SHA, версией промпта и моделями. Рубрика 0-2 и M1-M4 заполняются вручную.

```bash
.venv/bin/python -m insight_agent.evaluation manifest.jsonl --dry-run --out report.jsonl      # без LLM, строки NOT_RUN
.venv/bin/python -m insight_agent.evaluation manifest.jsonl --max-rub 100 --out report.jsonl  # живой прогон, нужен LLM_API_KEY
```

Без ключа строки получают `BLOCKED`, а не `PASS`.

## Зависимости

FastAPI, Uvicorn, Pydantic, pydantic-settings, HTTPX, pytest. Версии в `requirements.lock`. Модели только из приложения 3 (список в `llm.py`), имена задаются в `.env`.

## Реальные материалы D03/D04

Загрузка PDF/OCR, ручная проверка, версионирование новостей и запуск корпуса описаны в [material-ingestion.md](docs/material-ingestion.md). Подготовка: `insight-materials`; снимок подключается через `MATERIALS_DIR`. Исходные PDF, полные статьи, OCR и ключи не входят в Git. Локальные fixtures остаются синтетическими тестовыми данными.
