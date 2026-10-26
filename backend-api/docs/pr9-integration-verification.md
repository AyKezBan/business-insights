# PR #9: интеграция с develop после PR #8

Дата проверки: 26.10.2026. Локальная ветка: `feature/feed-refresh-and-event-versioning`.
Исходный PR #9: `c4a8f7cdc61fc93f9bf62863b555ecea8086c043`.
Включённый develop: `02898a9400cdf806bbe90631ad832c6614992dfc` (с PR #8).
Обе удалённые ветки повторно проверены после тестов; их HEAD не изменился.

## Результат объединения

- Trusted catalog, полный publication token, CAS, frozen profile/cutoff/corpus context, outcome, claim IDs, evidence spans, QA, PDF provenance и обратная связь из #8 сохранены. Обновление ленты, объяснимая релевантность, expiry, история событий и stale из #9 работают поверх этого каталога.
- `/v1/materials/events` сохраняет cursor-контракт #8. Offset-контракт новостей вынесен в `/v1/materials/news`; он читает актуальную либо явно закреплённую revision, без модели и сбора источников. Java refresh использует полный проверенный каталог, а не несовместимый news bridge.
- Профиль, событие, версия, роль и контекст ML сохраняются до очереди в одной транзакции. Повтор refresh переиспользует операцию; executor получает её после commit. Opaque material IDs допускаются. Коррекция semantic hash создаёт версию и делает старые операции stale; обновление только банковского корпуса версию события не увеличивает.
- Миграция `V2__material_catalog_context.sql` из develop не изменена. Миграция #9 переименована в `V3__event_versions_and_feed.sql`. Проверены чистая установка и переход с заполненной V2 с сохранением результата, профиля и feedback.
- Согласованы Java DTO, OpenAPI и JSON Schema. Все 13 общих схем соответствуют экспорту Pydantic, все 9 ресурсных копий backend совпадают с общими побайтно. Версия адаптера — `materials-feed-v3`; прежний commit в metadata явно обозначает историческую базу.
- В автоматически объединённых файлах устранены дубли полей/аргументов профиля и согласована запись каталога с историей событий. Исправлена привязка Spring configuration properties при нескольких конструкторах. `FEED_MAX_AGE` передаётся из общего Compose.
- Для воспроизводимости shell/JSON/YAML fixtures закреплены LF. Smoke поддерживает Windows, проверяет metadata обеих веток и использует конечный синтетический сценарий с точными цитатами и semantic review.

## Выполненные проверки

| Проверка | Результат |
| --- | --- |
| Java 21 / Maven `test package` | **138 passed**, 0 failures/errors/skips; собран Spring Boot JAR |
| PostgreSQL 17.11 | Реальные disposable DB-тесты: миграция V2→V3, хранение и история, CAS, очередь с закреплённым профилем, идемпотентный refresh, доступ, opaque IDs, stale и feedback |
| ML / Python 3.12 в Linux | **936 passed**, 0 skipped; сеть контейнера отключена |
| Frontend Python | **20 passed**, 0 skipped |
| Frontend Node | **Все 6 suites прошли** |
| Importer | **7 passed**, 0 skipped, включая настоящий PostgreSQL |
| Проверка JUnit guard | **5 passed**; итоговые Python/Java XML дополнительно проверены на отсутствие failures/errors/skips и наличие обязательных DB-классов |
| Контракты | 13 экспортированных схем; 9 побайтно одинаковых копий; OpenAPI разобран без duplicate keys, ссылки и публичные DTO проверены тестом |
| Docker build и полный smoke | **PASS**: все образы, материалы, импорт, HTTP/gateway, анализ, QA, источники, polling, лента, hidden/opened, feedback, local action, traces, повтор импорта, отказ другого dataset и сохранность после рестарта |
| Git | Нет конфликтных маркеров; `git diff --check` без ошибок |

Java запускался с `BI_JDBC_TEST_URL`, указывающим на отдельный PostgreSQL-контейнер без persistent volumes. ML выполнялся в контейнере с установленными Git и Docker Compose для offline-тестов CLI; Docker socket и сеть ему не предоставлялись. Поэтому проверки не были скрыто пропущены из-за отсутствующих зависимостей. Внутренние Java-тесты при Docker build могут пропускать DB-кейсы; отдельный полный запуск Maven выше выполнил их без пропусков.

Основные команды: `mvn -o -B -ntp -f backend-api/pom.xml -DfailIfNoTests=true test package`, `python -m pytest -q` в ML/Frontend/importer, `node tests/test_*.cjs` по каждому файлу, `python -m unittest discover -s infra/ci/tests -v`, `python infra/smoke/run.py`. Для ML использованы зафиксированные `requirements.lock` и Linux; importer/Java получили отдельные тестовые подключения.

Локальные журналы остаются в игнорируемой `.local`: `pr9-java-tests.log`, `pr9-ml-tests.log`, XML в `pr9-reports/` и итоговый `smoke-ebqqspcw/report.json` с `commands.log`. Успешный smoke project — `bi-smoke-e84dea443e`; его контейнеры и volumes удалены самим runner. Предыдущие неуспешные smoke-запуски сохранены в журнале: они обнаружили configuration binding и устаревшие синтетические ответы без нужной semantic review/точной цитаты. После исправлений повторный полный запуск прошёл. Проверки продукта ради fixture не отключались.

## Оставшиеся границы проверки

Живая LLM не вызывалась, реальные клиентские CSV и приватный корпус не использовались. Качество смысловых выводов и правил релевантности этим запуском не подтверждается. Единственное предупреждение ML — прежняя deprecation Starlette/httpx в TestClient. Браузерная проверка на реальных материалах, независимая смысловая приёмка и воспроизведение вторым человеком (G08/G09/G10 в `docs/materials-acceptance.md`) остаются открытыми; эта интеграция их не объявляет пройденными.

Результат подготовлен локально. Push и merge PR на GitHub не выполнялись.
