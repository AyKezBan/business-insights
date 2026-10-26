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
docker build -t insight-agent ml-service
docker run --rm -p 8000:8000 --env-file ml-service/.env -v "$PWD/ml-service/data:/app/data:ro" insight-agent
```

Один запрос без сервера (без ключа LLM вернётся `status: failed`):

```bash
.venv/bin/insight-agent tests/fixtures/request.json
```

## API

- `GET /health`
- `GET /v1/materials/events?limit=20&offset=0` — страница новостей загруженного корпуса без LLM-вызовов. Возвращает `corpus_revision`, `items` с доказательствами и конфликтами, `next_offset`. Используется backend при явном обновлении ленты.
- `POST /v1/insights` - инсайт по событию
- `POST /v1/insights/{insight_id}/questions` - уточняющий вопрос по карточке
- `GET /v1/runs/{run_id}/trace` - трасса запуска

Схемы запросов и ответов лежат в [contracts/ml](../contracts/ml). Если поменяли `schemas.py`, их надо перевыгрузить:

```bash
.venv/bin/python -m insight_agent.export_schemas
```

## Данные

- `BANK_FRAGMENTS_PATH` (по умолчанию `data/bank_fragments.jsonl`) - фрагменты аналитики банка
- `NEWS_PATH` (`data/news.jsonl`) - новости
- `TRACE_DIR` (`traces`) - трассы, один файл на запуск

`data/` и `traces/` в гит не коммитим. В `tests/fixtures` только выдуманные данные.

## Тесты

```bash
.venv/bin/pytest
```

Вместо LLM в тестах подставляется фейковый клиент (`tests/fakes.py`), ключ не нужен.

## Зависимости

FastAPI, Uvicorn, Pydantic, pydantic-settings, HTTPX, pytest. Версии в `requirements.lock`. Модели только из приложения 3 (список в `llm.py`), имена задаются в `.env`.

## Реальные материалы D03/D04

Загрузка PDF/OCR, ручная проверка, версионирование новостей и запуск корпуса описаны в [material-ingestion.md](docs/material-ingestion.md). Подготовка: `insight-materials`; снимок подключается через `MATERIALS_DIR`. Исходные PDF, полные статьи, OCR и ключи не входят в Git. Локальные fixtures остаются синтетическими тестовыми данными.
