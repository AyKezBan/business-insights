# Материалы D03/D04

Реализация расширяет агент из `feature/ml-insight-agent` (PR #3). CSV и PostgreSQL остаются в зоне data-import (PR #2). Java/UI подключают существующие ML-контракты; новый backend для них не создаётся.

## Установка и подготовка

Python 3.12, Poppler (`pdftoppm`), Tesseract с `rus` и `eng`. В контейнере они устанавливаются Dockerfile. В репозитории есть manifest девяти исходных PDF; сами PDF, OCR, полные новости, review-файлы и ключи хранятся вне Git.

Из корня репозитория:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r ml-service/requirements.lock
.venv/bin/python -m pip install --no-deps -e ml-service
python -m insight_agent.materials.cli bank --pdf-dir /absolute/pdf \
  --manifest ml-service/config/bank-documents.json --store /absolute/materials
```

Активируйте venv или используйте `.venv/bin/python` вместо `python`. Первый импорт возвращает **2**: кандидаты требуют проверки. `staging/bank-candidates.jsonl` содержит ID, страницу, текст, offsets, hash. Сверьте утверждение с изображением страницы; разрешайте только законченные предложения. Таблицы, графики, колонки, потерянные отрицания и числа особенно требуют проверки.

Review — JSON-словарь `fragment_id → решение`:

```json
{
  "COPY_EXACT_FRAGMENT_ID": {
    "content_sha256": "COPY_EXACT_64_HEX_HASH",
    "review_status": "verified",
    "statement_type": "bank_assessment",
    "target_period": null,
    "valid_until": null,
    "supersedes": []
  }
}
```

Типы: `reported_fact` — опубликованная статистика, `bank_assessment` — собственная оценка банка, `forecast` — прогноз. Для прогноза обязательно задайте `target_period` и `valid_until`; не объединяйте разные горизонты. `supersedes` — ID заменённого прогноза, только после ручной сверки одного показателя и горизонта. `mixed` и непроверенный текст не подходят для позиции банка.

Исправление OCR: скопируйте полный текст страницы из указанного в manifest пути `pages/.../NNNN.txt` в `/absolute/corrections/<doc_id>/NNNN.txt`, исправьте по оригиналу и добавьте `--text-overrides /absolute/corrections`. Появится новая immutable версия страницы и hash исправления в `tool_versions`; предыдущая версия сохранится. Review привязан к hash конкретного фрагмента: изменённые утверждения нужно проверить заново. Исходный PDF всегда остаётся оригиналом.

```sh
python -m insight_agent.materials.cli bank --pdf-dir /absolute/pdf \
  --manifest ml-service/config/bank-documents.json --review-file /absolute/reviews.json \
  --text-overrides /absolute/corrections --store /absolute/materials
python -m insight_agent.materials.cli news --source cbr --limit 10 --store /absolute/materials
python -m insight_agent.materials.cli check --store /absolute/materials
```

`news --saved /absolute/news.jsonl` читает массив JSON или JSONL `RawNews`: `url,title,text,published_at,retrieved_at`. Даты со смещением часового пояса; неизвестная публикация — `null`, дата загрузки её не заменяет. RSS ЦБ читается с первичным текстом статьи и оригинальным URL. Сбой статьи/сети прерывает batch и сохраняет прежний снимок.

Точные копии нормализованного текста объединяются, URL сохраняются. Изменение одного URL создаёт новую `SourceVersion`; предыдущие тексты сохраняются. Разошедшиеся источники остаются с `conflicts`, результат агента получает `limited`. Семантические перефразы автоматически не объединяются. Уже существующие event IDs сохраняются даже при последующем совпадении двух ранее разных событий. Для явной группировки:

```sh
python -m insight_agent.materials.cli link --event-id EXISTING_EVENT_ID \
  --source-id ACTIVE_SOURCE_ID --store /absolute/materials
```

Коды legacy-команд: **0** — опубликовано/проверено; **2** — нужны review/данные, прежний снимок сохранён; **1** — ошибка источника, OCR или целостности. Публикация атомарна: immutable snapshot + замена `current.json`. Все writers используют общую reentrant-блокировку `store.corpus_lock`; `publish_snapshot(root, snapshot) -> str` сам её берёт. Для read/merge/write держите эту же блокировку на протяжении всей транзакции, включая повторное чтение current. `cli.corpus_lock` сохранён как alias. Чтение и обслуживание пока закрепляют одну revision; после импорта нужен restart ML.

## Несколько издателей: sync

```sh
python -m insight_agent.materials.cli sync \
  --sources cbr_press,fns_news,eia_today --limit-per-source 5 --store /absolute/materials
```

Параметр принимает только ID фиксированного реестра, без повторов; лимит — целое число 1–100 на источник. Сбор и парсинг завершаются до writer lock. Под блокировкой команда заново читает последний snapshot, сохраняет банковский корпус и прежние новости, объединяет принятые записи и делает максимум одну публикацию. Ошибка отдельной статьи не отменяет остальные; пустой discovery не считается healthy. Все failed/blocked источники сохраняют прежние данные.

Коды **sync**:

| Код | status | Результат |
| --- | --- | --- |
| 0 | complete / unchanged | Успешная публикация либо подтверждённый неизменный корпус |
| 1 | failed | Все источники недоступны либо ошибка корпуса/публикации/report |
| 2 | needs_review | Нет пригодных данных; новый snapshot не публикуется |
| 3 | partial | Часть источников/статей недоступна или пуста; полезные данные опубликованы либо доступны в прежнем корпусе |

Код **3 не означает «ничего не опубликовано»**. JSON stdout содержит `previous_revision`, `published_revision` (null, если новой публикации не было), `current_revision`, `previous_token`, `catalog_token`, `health`, `sources`, `fatal_errors` и `report_path`. В `sources`, по registry ID, находятся publisher ID, status/health, article counters, last-success, item errors, active-record counts, source-version IDs и hashes. Отдельные ошибки источника не записываются в `snapshot.report.errors`; фатальная ошибка целостности по-прежнему блокирует публикацию.

`runs/<run_id>.json` — неизменяемый итоговый отчёт. `last_attempt.json` атомарно указывает на него и содержит SHA-256. Отчёт сериализуется и сохраняется во временный `.pending` файл **до** публикации. Если финальная запись отчёта/last-attempt не удалась уже после замены current, команда возвращает **1**, но сообщает фактически опубликованную revision/token и не откатывает её. Если возможно, создаётся отдельный failed report с `related_run_id`; старый immutable report не меняется. При недоступном диске `report_path` может быть null. Прерванная попытка может оставить staged/orphan-файлы; current всегда указывает на целый проверенный snapshot.

CLI хранит private HTTP representations в `<store>/http-cache`. Повтор посылает `If-None-Match`/`If-Modified-Since` при наличии validators; 304 используется только с проверенным cached body того же URL и parser version. Даже 304 списка приводит к повторной проверке отдельных статей. При полностью неизменных ответах сохраняются исходные retrieved_at, IDs, counts, revision и generation; новые health/check times живут только в отчёте. Свежий 200 с прежним содержимым может обновить observation time и corpus revision/generation, но не event/source-version identity. Ошибки HTTP, 403 и challenge не заменяются stale-cache успехом. Сетевые ограничения/retry остаются в общем ControlledTransport; тела и validators не сохраняются в Git.

Для программного вызова с тем же кешем:

```python
from datetime import datetime, timezone
from pathlib import Path
from insight_agent.materials.collector import collect_sources, sync_sources
from insight_agent.materials.registry import get_spec

root = Path('/absolute/materials')
specs = [get_spec(name) for name in ('cbr_press', 'fns_news', 'eia_today')]
batches = collect_sources(specs, 5, cache_dir=root / 'http-cache')
result = sync_sources(root, batches, datetime.now(timezone.utc))
```

Двухаргументный `collect_sources(specs, limit_per_source)` также поддержан: без `cache_dir` representations между вызовами не сохраняются. Для offline tests можно передать keyword `transport=ControlledTransport(...)` с fake HTTP/clock; один transport используется всеми адаптерами. Реальный EIA discovery — ровно `https://www.eia.gov/todayinenergy/`, статьи — `detail.php?id=...`; `recent.php`, archive, RSS и остальные пути под этим prefix запрещены. Исключение discovery не открывает весь subtree.

## Publication token и историческое чтение

`current.json` содержит один `CatalogToken(store_id, generation, revision)`. Writer закрепляет store ID в `store.json` один раз; generation растёт при любой смене опубликованного корпуса, включая bank-only/metadata updates без изменения event hash. Точный replay не меняет pointer bytes/token. Повторная публикация старой revision после другой публикации получает новую generation. `publications/<generation:020d>.json` архивирует только **предыдущий уже committed** token до смены current; неуспешная будущая публикация никогда не попадает в этот журнал.

Read-only APIs:

```python
from insight_agent.materials.store import load_head, load_snapshot, load_catalog_token

head = load_head(root)                 # StoreHead(snapshot, token), или None без current
old = load_snapshot(root, revision=known_revision)
token = load_catalog_token(root, revision=known_revision)
```

`load_head` читает pointer один раз и возвращает согласованную пару. Историческое чтение не меняет current. `load_catalog_token` возвращает последнюю committed публикацию указанной revision; для валидного, но не опубликованного v2 snapshot — KeyError. `load_snapshot(revision=...)` проверяет immutable bytes, само наличие такого файла не доказывает publication. V1 читается без записи с sentinel `store_id="legacy", generation=0`; первый явный writer publish создаёт v2/token и сохраняет исходные v1 bytes. Sentinel не используется для trusted current-catalog sync. Новая runtime hot-reload/CAS интеграция принадлежит следующему этапу T07.

## Контейнер

Скопируйте `ml-service/.env.example` в `ml-service/.env` и заполните `LLM_API_KEY` локально. Ключ из `task/.env` переносится только через окружение: `API_KEY → LLM_API_KEY`, `BASE_URL → LLM_BASE_URL`; `MODEL` можно использовать для разрешённой генераторной модели. Не сохраняйте значения в Git или shell history. Полные model IDs указаны в примере env. Общий лимит шлюза 500 руб./день со сбросом в 03:00 МСК проверяется отдельно; token guard этого не доказывает.

```sh
mkdir -p materials materials-review traces
export LOCAL_UID="$(id -u)" LOCAL_GID="$(id -g)"
export PDF_DIR=/absolute/pdf
# Paths in compose are relative to infra; these are explicit host paths.
export MATERIALS_STORE="$PWD/materials" MATERIALS_REVIEW_DIR="$PWD/materials-review" TRACES_DIR="$PWD/traces"
docker compose -f infra/compose.ml-materials.yml build ml
docker compose -f infra/compose.ml-materials.yml run --rm prepare bank \
  --pdf-dir /inputs --manifest /config/bank-documents.json --store /app/materials
# После проверки положите reviews.json в materials-review.
docker compose -f infra/compose.ml-materials.yml run --rm prepare bank \
  --pdf-dir /inputs --manifest /config/bank-documents.json --review-file /review/reviews.json --store /app/materials
docker compose -f infra/compose.ml-materials.yml run --rm prepare news --source cbr --limit 10 --store /app/materials
docker compose -f infra/compose.ml-materials.yml up -d ml
curl http://localhost:8000/health
```

Для corrections положите каталог в `materials-review/corrections`, передайте `--text-overrides /review/corrections`. После обновления `docker compose -f infra/compose.ml-materials.yml restart ml`.

`/health` сообщает corpus_revision, news, verified_bank_fragments. Если MATERIALS_DIR задан, повреждённый/отсутствующий snapshot останавливает startup. Без него сохранён legacy fixture-режим upstream для тестов; это не production corpus.

## Новый запрос и ссылки

Профиль должен соответствовать `contracts/ml/insight-request.schema.json`, пример — `contracts/ml/examples/profile-from-import.json`. Поле revenue_status=`observed` из data-import отображается в `known`; год 2025, валюта RUB, scope неизвестен, если не указан. 1000 — placeholder, 0 и 999 — наблюдения. Нет ФТС не доказывает отсутствие торговли, платёжные контрагенты не размечены как покупатели/поставщики.

Создать request для уже импортированной новости без LLM:

```python
import json
from pathlib import Path
from insight_agent.materials.store import load_snapshot
from insight_agent.schemas import InsightRequest
snapshot = load_snapshot(Path('/absolute/materials'))
profile = json.loads(Path('/absolute/profile.json').read_text())
request = InsightRequest(profile=profile, role='cfo', event=snapshot.news[0])
Path('/absolute/request.json').write_text(request.model_dump_json(indent=2))
```

Отправка: `curl -H 'Content-Type: application/json' --data-binary @/absolute/request.json http://localhost:8000/v1/insights`. Это делает реальные LLM-запросы и расходует общий бюджет.

Ссылка банка: `/v1/materials/documents/<doc_id>/file#page=N`, внешняя — оригинальный URL. `MATERIALS_PUBLIC_BASE_URL` должен быть адресом, доступным браузеру через reverse proxy. PDF обслуживается только по registry snapshot, без произвольных путей. UI использует явный `SourceRef.url` и принимает opaque IDs с `:`/`_`; массивы claims и соответствие ID сохраняются.

Поиск банка: verified, дата публикации не позже события, окно 180 дней, применимый горизонт; неизвестные/будущие/устаревшие документы и истёкшие/заменённые прогнозы исключаются. Если дата события неизвестна, используется текущая дата UTC с `limited`. Отсутствие покрытия — `unavailable`, а не придуманная позиция банка.

Проверки чисел, единиц, source IDs и типов детерминированы; они не доказывают смысловое следование. Семантическое качество оценивается отдельно. Полные исходные тексты сохраняются; сокращённый LLM-контекст отмечается в missing_data. QA проходит те же проверки; публичный текст строится из оставшихся claims.

Live-проверка конфигурации: Gemma возвращает JSON для обеих ролей; пример env использует её. Qwen в ограниченном прогоне не ответил за 55 секунд, GLM при cap 4500 потратил ответ на reasoning без JSON. Эти настройки нельзя считать проверенной заменой Gemma; перед сменой модели отдельно проверьте timeout, output budget и стоимость. Live-ответы всё равно требуют смысловой оценки, даже когда схема валидна.
