# Release-runbook D03/D04

Инструкция для текущей реализации, объединённой по [плану 26 октября](superpowers/plans/2026-10-26-domains-03-04-finalization.md). Выполняйте команды из корня одного checkout. Зафиксируйте `git rev-parse HEAD` до проверки. Фактические gates и run IDs ведутся в [приёмке](materials-acceptance.md); [результаты 24 октября](materials-acceptance-2026-10-24.md) и [DevOps smoke](../infra/VERIFICATION.md) относятся к их историческим версиям. Эта инструкция не объявляет live 9+9, ручную приёмку или весь выпуск принятыми.

## 1. Подготовить приватные входы

Нужны Docker daemon, Compose **2.24.4+**, Python 3 и доступ к registry/Maven/PyPI при сборке. Host OCR/Python 3.12 нужны только для самостоятельного ML-пути; Compose устанавливает Poppler и Tesseract rus+eng в образ. CSV, PDF, OCR, полные статьи, профили клиентов, live outputs и ключи держите вне Git. `.env`, `.local`, `materials`, `materials-input` и `materials-review` в корне игнорируются; произвольный другой каталог в checkout автоматически приватным не становится.

```sh
git rev-parse HEAD
./stack init
```

`init` создаёт `.env` с локальными случайными credentials, правами 0600 и не перезаписывает существующий файл. В локальном редакторе задайте:

| Поле `.env` | Значение |
| --- | --- |
| `DATA_DIR` | Абсолютный существующий каталог с `df_Uid_data_hack.csv`, `df_Fid_Uid_link_hack.csv`, `df_Uid_payment_hack.csv` |
| `PDF_DIR` | Абсолютный каталог оригинальных девяти PDF с именами и hashes из `ml-service/config/bank-documents.json` |
| `MATERIALS_STORE` | Абсолютный каталог назначения корпуса, например `/absolute/private/materials` |
| `MATERIALS_REVIEW_DIR` | Абсолютный каталог review/corrections, например `/absolute/private/review` |
| `BACKEND_DEMO_PERSON_ID` | ФЛ, существующий в CSV-связях с нужными компаниями; default `F_1` |
| `WEB_PORT` | Свободный локальный порт; default `8088` |
| `MATERIALS_PUBLIC_BASE_URL` | Browser-facing HTTP(S) origin; пустое значение даёт `http://127.0.0.1:WEB_PORT` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_*` | Разрешённый шлюз/ключ/модели для явно запущенной генерации |

Создайте **свои** каталоги store/review заранее; bind mounts с `create_host_path: false` не создают отсутствующие пути. На Linux job пишет под UID/GID вызывающего пользователя; serving ML uid1000 должен читать store и все его parent directories. CSV/PDF/review монтируются read-only, store для jobs — read-write. Отдельный `ml-service/.env` для root Compose не нужен. Не выводите раскрытый `docker compose config` и не переносите значения ключа в команды или Git.

```sh
./stack config
GIT_SHA="$(git rev-parse HEAD)" docker compose -f compose.yaml -f infra/compose.materials.yaml --profile jobs config --quiet
GIT_SHA="$(git rev-parse HEAD)" docker compose -f compose.yaml -f infra/compose.materials.yaml build materials
```

Последняя команда явно пересобирает CLI перед подготовкой; `./stack materials` сам не гарантирует пересборку ранее существующего образа. Root gateway — локальное однопользовательское demo с server bearer, не банковская авторизация. Доступен только `127.0.0.1:WEB_PORT`; браузер не получает bearer или ключ модели.

## 2. Подготовить и проверить банковский корпус

```sh
./stack materials bank --store /app/materials --pdf-dir /inputs \
  --manifest /config/bank-documents.json
```

На первом импорте без решений ожидается exit **2**: кандидаты записаны в `<store>/staging/bank-candidates.jsonl`, но публикация не разрешена. **2 — не PASS** и не повод помечать всё verified. Сверьте каждое разрешаемое утверждение с физической страницей оригинального PDF; таблицы, колонки, отрицания, единицы и горизонты проверяйте отдельно. Наличие текста и hash не доказывает правильность OCR.

Поместите `bank-review.json` в `MATERIALS_REVIEW_DIR`. Формат решения — `fragment_id → {content_sha256, review_status, statement_type, target_period, valid_until, supersedes}`; [полный пример и correction-путь](../ml-service/docs/material-ingestion.md). Разрешены только hash-bound `verified` с датой документа и типом `reported_fact`, `bank_assessment` или `forecast`; forecast требует применимого горизонта. `mixed` и pending не считаются позицией банка. Дата отчётного периода не заменяет неизвестную дату публикации.

```sh
./stack materials bank --store /app/materials --pdf-dir /inputs \
  --manifest /config/bank-documents.json --review-file /review/bank-review.json
./stack materials check --store /app/materials
```

Для ручных corrections повторите bank с `--text-overrides /review/corrections`: структура `<doc_id>/NNNN.txt`, полный исправленный текст страницы. Изменённые фрагменты требуют нового review; исходный PDF не заменяется. Сохраните snapshot/страничные hashes и dispositions всех 64 физических страниц отдельно от числа verified фрагментов. CLI принимает fragment review, но не доказывает полноту визуальной проверки всех страниц.

Если воспроизводите уже проверенный корпус, используйте **точные** исходный snapshot, OCR/tool versions и визуальные witnesses. `ml-service/eval/rebuild_bank_review.py` умеет перенести проверенные metadata в новый private store без OCR; он отвергает несовпадение PDF/page/extraction/fragment hashes и требует новый destination вне source. Проверить интерфейс: `PYTHONPATH=ml-service/src /absolute/venv/bin/python ml-service/eval/rebuild_bank_review.py --help`. Repository review/gold metadata не являются оригиналами PDF и не делают новый OCR автоматически проверенным.

## 3. Собрать три издателя и запустить систему

```sh
./stack materials sync --store /app/materials \
  --sources cbr_press,fns_news,eia_today --limit-per-source 5
./stack materials check --store /app/materials
./stack materials-up
./stack status
```

`cbr_press` — ЦБ (`cbr`), `fns_news` — ФНС (`fns`), `eia_today` — EIA (`eia`). Это фиксированный registry; произвольные URL не принимаются. Sync читает сеть без вызовов модели, сохраняет прежний bank/news corpus и публикует максимум один новый snapshot. Limit — 1–100 на источник. Legacy `news --source cbr` остаётся отдельной одноканальной диагностикой, не заменяет этот сбор трёх издателей.

| Exit sync | Значение и действие |
| --- | --- |
| 0 | complete/unchanged; проверить publisher counters, revision и token |
| 1 | failed; проверить report, диск и целостность. Если сбой report случился после commit pointer, новая revision уже могла быть опубликована |
| 2 | needs_review/нет пригодных данных; прежний snapshot сохранён |
| 3 | partial; часть источников/статей недоступна, полезная публикация или прежние данные могут существовать. Не объявлять три healthy publishers |

JSON CLI содержит `current_revision`, `catalog_token`, per-source counters/errors и `report_path`. Итоги лежат в `<store>/runs/<run_id>.json`; `last_attempt.json` содержит checksum. HTTP representations в `<store>/http-cache` приватны. 304 и неизменённый корпус не создают новое событие; correction создаёт source version. Partial/network failure не подменяется фиктивным успехом.

Откройте [локальный UI](http://127.0.0.1:8088) (или свой `WEB_PORT`). Compose ждёт PostgreSQL → импорт CSV → Flyway/Java → gateway; ML стартует с проверенным `current.json`. При materials overlay повреждённый/отсутствующий корпус останавливает startup, fixtures вместо него не включаются.

```sh
# Внутренняя health/read-only диагностика: ML health наружу не проксируется.
docker compose -f compose.yaml -f infra/compose.materials.yaml exec -T ml \
  python -c 'import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen("http://127.0.0.1:8000/health")), ensure_ascii=False))'
curl --fail-with-body 'http://127.0.0.1:8088/api/v1/companies?limit=1'
curl --fail-with-body 'http://127.0.0.1:8088/api/v1/materials/events?limit=1&offset=0'
```

Замените порт в curl, если он изменён. Health показывает `current_revision`, `serving_revision`, `catalog_initialized`, полный `catalog_token`, `verified_bank_fragments`, `source_health`, `last_attempt`, `last_refresh_error`. HTTP 200 health означает доступность процесса; `status=degraded` и per-source report всё ещё требуют внимания. Health не проверяет ключ/тариф/качество модели.

## 4. Явно выбрать анализ, открыть источники и задать QA

В UI выберите компанию и роль, откройте **каталог материалов**, затем нажмите анализ выбранного материала. Каталог синхронизируется Java из ML при чтении `GET /api/v1/materials/events`. Эта операция не вызывает модель и не наполняет автоматически **ленту подготовленных инсайтов**. Poller тоже не запускает массовые анализы по клиентам.

Для API-клиента возьмите из ответа каталога `items[].eventId`, `items[].eventContentSha256` и `catalogToken.revision`; значения opaque, не разбирайте ID по `:`. Отправьте в `POST /api/v1/analyses` JSON со своими точными значениями:

```json
{
  "companyId": "U_1",
  "role": "cfo",
  "eventId": "COPY_EVENT_ID",
  "corpusRevision": "COPY_64_HEX_REVISION",
  "eventContentSha256": "COPY_64_HEX_EVENT_HASH",
  "requestId": "new-release-analysis-1",
  "userProvided": {}
}
```

Это шаблон, замените company и placeholders реальными значениями. Сохраните payload вне Git. Следующая команда **расходует бюджет модели**, если генерация разрешена:

```sh
curl --fail-with-body -i -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: new-release-analysis-1' \
  --data-binary @/absolute/private/analysis.json \
  http://127.0.0.1:8088/api/v1/analyses
```

Ответ 202 содержит `Location: /api/v1/operations/<UUID>` и `Retry-After`. Читайте этот URL до terminal status, не повторяйте POST для polling. Один key с теми же immutable inputs возвращает ту же операцию; изменённые inputs с прежним key получают 409. Для нового анализа используйте новый key и requestId. Backend сохраняет event/profile/role/cutoff/revision/hash **до очереди**. Обновление каталога в ожидании worker не меняет уже принятые входы. Legacy `POST /api/v1/events` остаётся immutable и не служит correction-upsert.

В карточке проверьте outcome отдельно от operation status: `full/limited/refused/none`; в QA — `answered/no_answer`. `refused`, отсутствие ответа или ошибка не разрешают показ непроверенного черновика. Claims содержат ID и evidence spans: source hash и Unicode offsets `[start,end)`, bank span — полный проверенный фрагмент. Проверка hashes/границ дополняется semantic gate итогового текста и QA в общем budget/deadline. Ошибка/неполный verdict/исчерпание бюджета не разрешают публикацию unchecked текста. Это механизм ограничения ошибок, а смысловая корректность реального выпуска требует отдельной ручной приёмки.

Откройте original article по внешнему URL и PDF по выданному `SourceRef.url`: абсолютный browser-facing адрес `/v1/materials/documents/<doc_id>/file?revision=<sha256>#page=N`, где N — физическая страница PDF. Gateway проксирует только document routes ML; generation/refresh/trace ML снаружи не открыты. Trace читайте через Java `/api/v1/insights/<UUID>/trace`. Сопоставьте claim ID, excerpt, content hash, publisher и перевод EIA с trace и оригиналом. PDF отдаётся из immutable store по registry, OCR text не заменяет оригинал.

`asOf` — **publication cutoff**, не исторический point-in-time replay. Поздняя correction с прежней publication date может присутствовать в выбранной нынешней revision; предупреждение/limited обязательно сохраняются. Day-only/unknown даты не становятся выдуманным точным timestamp. Повтор принятого анализа и QA закрепляются за сохранённой revision. Если её файлы недоступны, QA возвращает безопасный `no_answer`, а не подставляет сегодняшний корпус. Не удаляйте старые snapshots/raw PDF, пока нужны старые результаты.

## 5. Обновление, poller и перезапуск

Ручной sync из шага 3 публикует snapshot атомарно под общей writer lock. Runtime проверяет current pointer при новом текущем чтении; новый run получает новую revision, начавшийся run и старый QA сохраняют свою. Перезапуск ML после нормальной публикации **не нужен**. Явная диагностика refresh выполняется только внутри сети Compose:

```sh
docker compose -f compose.yaml -f infra/compose.materials.yaml exec -T ml \
  python -c 'import urllib.request; r=urllib.request.Request("http://127.0.0.1:8000/v1/materials/refresh", data=b"", method="POST"); print(urllib.request.urlopen(r).read().decode())'
./stack materials-watch
./stack logs materials-poller
./stack status
```

`materials-watch` — явный optional запуск supervisor. Сразу выполняет sync трёх издателей по пять материалов, затем ждёт **900 секунд после окончания** попытки. `MATERIALS_POLL_INTERVAL_SECONDS` меняет паузу (≤3600), `MATERIALS_POLL_TIMEOUT_SECONDS` — timeout попытки (default 600). Коды 1/2/timeout дают backoff 1800/3600, 0 сбрасывает его, 3 оставляет обычную паузу, но не сбрасывает накопленный backoff. Статус — `<store>/poller-status.json`, подробности источников — immutable CLI reports. `.poller.lock` не удаляйте: он предотвращает второй poller в том же store на POSIX flock FS. При занятом lock/ошибке конфигурации автоматического restart нет; исправьте причину и явно повторите `materials-watch`.

После обновления текущего каталога UI-пагинация по старой revision получает `409 CATALOG_CHANGED`: обновите каталог и выберите материал заново. Историческое чтение не откатывает current head; другой store ID требует отдельной согласованной reseed-процедуры.

```sh
./stack down                 # остановить также poller, сохранить БД/traces/store
./stack materials-up         # повторно подключить тот же corpus overlay
./stack materials-watch      # если нужен регулярный сбор
./stack import               # тот же CSV dataset → unchanged
```

Не заменяйте `materials-up` на обычный `up` для corpus-окружения. Не применяйте `down -v`: он удаляет named volumes PostgreSQL/traces. Private bind store хранится отдельно; резервируйте и его, и named volumes. Изменённый CSV dataset importer отклоняет; новый dataset запускайте в другом Compose project/порту, а не обходите защиту. Незавершённые Java операции после рестарта становятся FAILED; automatic retry не выполняется. Готовые inputs/results/feedback/opened/hidden хранятся в PostgreSQL, ML run state и ruble ledger — в trace volume; память не заменяет эти файлы. Ledger отражает расходы этой установки, а не полный остаток общего provider key и других участников.

## 6. Проверки и диагностика выпуска

```sh
./stack smoke
./stack logs import-data
./stack logs backend
./stack logs ml
git rev-parse HEAD
docker compose -f compose.yaml -f infra/compose.materials.yaml exec -T ml \
  python -c 'import os; print(os.environ.get("GIT_SHA", "unknown"))'
```

`smoke` использует отдельный disposable project, синтетические CSV/materials и scripted ML; не читает рабочий `.env`, не делает paid calls и очищает только свои volumes. Он проверяет транспорт/хранение и не является live quality evidence. Полные offline-команды: [E2E](materials-e2e.md), [CI](../infra/ci/README.md). У Java DB cases без dedicated test PostgreSQL статус SKIPPED/NOT_RUN; зелёный Maven сам по себе не закрывает DB gate.

| Симптом | Проверка/действие |
| --- | --- |
| Import/стартап не готов | Файлы CSV, person/company links, `import-data` logs; пароль в `.env` должен соответствовать уже созданной БД |
| Нет catalog/`catalog_uninitialized` | Overlay подключён, current опубликован, v2 token/store/generation; legacy v1 читается, но не даёт trusted current catalog до явной v2 публикации |
| `corpus_unavailable`/refresh error | `materials check`, права и hashes store; не редактировать pointer/snapshot вручную. Повреждённый новый bundle не становится рабочим |
| Частичный sync | Per-source article errors/last-success/counters; 403/challenge/пустой discovery не считать healthy |
| PDF 404 или неверный host | Сохранены raw PDF старой revision, правильный doc ID/revision, `MATERIALS_PUBLIC_BASE_URL` доступен браузеру; `ml:8000` не browser origin |
| `corpus_event_mismatch`/409 | Payload взят из одной catalog revision; после обновления перечитать каталог и создать новый запрос, не смешивать hash и revision |
| Отказ/нет ответа | Outcome/errorCode/missingData/trace и semantic/budget verdict; пустой ключ не включает mock |
| SHA образа отличается от checkout | Wrapper встраивает HEAD в ML/jobs при сборке overlay. Пересобрать через `materials-up`/build jobs; runtime env не меняет SHA старого образа |

В release evidence запишите общий git/image SHA, prompt/schema/policy/profile versions, полный CatalogToken, source URLs/hashes и counts трёх publishers, page dispositions/verified counts, run/QA IDs, browser и restart results, latency/budget и ручные verdicts. В Git допустимы только проверенные metadata без сырого текста/персональных данных/секретов. Для paid проверки применяйте текущую разрешённую policy из [release rubric](../ml-service/eval/release-rubric-2026-10-26.json), включая зафиксированный user override: прежний session cap 100 RUB и ожидание таблицы тарифов отменены. Общий gateway limit и per-run/token guards сохраняются. Фиксируйте доступный остаток ключа, разрешённую модель, фактические usage/cost metadata и их валюту; defaults кода не являются подтверждёнными тарифами. Невыполненные live/browser/second-operator шаги остаются NOT_RUN/BLOCKED с владельцем.
