# Ветки и интеграция D03/D04: актуальный аудит

Дата: **26 октября 2026, Москва**. Это исследование и предлагаемый backlog; продуктовый код, ветки, merge, PR, платные LLM и общие данные в рамках этого отчёта не изменялись. Запись выполнена только в этот файл вне Git-каталога `business-insights`. Проверки выполнялись по подготовленным неизменяемым снимкам, с существующим Python-окружением, без установки зависимостей. Ruflo/ToolSearch отсутствуют в доступном каталоге инструментов; вымышленные вызовы не выполнялись.

## 1. Что изменилось с аудита 24 октября

**D03/D04 уже находятся в `origin/develop` вместе с data-import, исходной ML-реализацией и Java Backend. Повторять их разработку или повторно переносить старый worktree не требуется.** Основной ближайший риск — сохранность доказательств и контрактов при подключении новой ML-ветки и интерфейса. Новый ML уже учитывает часть ограничений Java; Java при этом закреплена на исходном ML-коммите и не передаёт часть данных материалов.

| Прежнее утверждение / источник | Наблюдение 26.10 | Следствие |
| --- | --- | --- |
| `docs/research/03-branches-audit.md`: develop — каркас, backend отсутствует | `develop=e105b7c` содержит data-import, ML, D03/D04 и Java | Старый аудит полезен как история решений, но не как текущая карта готовности |
| D03/D04 — отдельная ещё не объединённая реализация | Merge PR #4 `d2dbbb3`; head материалов `62e157b` — предок develop | Новые задачи строить от актуального develop; старый локальный `a3e5e14` уже не интеграционная база |
| ML умеет только готовые JSONL | Уже в develop есть PDF/OCR/review/snapshot/CLI и CBR/saved news ingestion; в ML-finalize добавлено request-time исследование | Не создавать второй ingestion-пайплайн, общий поиск источников и новый сервис с нуля |
| Backend ещё не написан (`ml-finalize:ml-service/docs/integration.md`) | Java merge PR #5 `5c41d77`, реализация `5572d5a` | Этот фрагмент документации ML устарел; источник истины о Java — код и OpenAPI develop |
| UI — только демо | Ветка `7c59dd0` добавляет явную server/demo границу, ожидание операций и безопасность источников | Транспорт подготовлен, реальные Java DTO bindings ещё отсутствуют; по умолчанию всё ещё demo |
| 115 тестов материалов подтверждают готовность | Сегодня повторно проходят 115 в develop и 242 в ML-finalize | Это офлайн-регрессия. A10, браузерная цепочка PDF и совместная приёмка не становятся принятыми от роста тестов |
| Исходные CSV/PDF нужно получить заново | Предыдущая приёмка описывает локальный корпус 9 PDF / 64 страницы и 12 CBR-статей | Проверить актуальный corpus manifest и хранение, не копировать локальный корпус/секреты в репозиторий |

## 2. Зафиксированные версии и правила сравнения

Короткие ссылки далее: **D** — снимок develop; **M** — ml-finalize; **U** — UI clarity. Все пути после префикса — относительно корня соответствующего snapshot. Файлы находятся в `/Users/selebry/Documents/dev/alpha-hack/generated/finalization-research-2026-10-26/{develop,ml-finalize,ui-clarity}`. Значимые утверждения привязаны к этим SHA, а не к будущим состояниям branch names.

| Обозначение / ref | Полный SHA | Значение |
| --- | --- | --- |
| D — `origin/develop` | `e105b7cda044b9ea69b0e37e3394decc1f59b014` | Текущая общая база после PR #2–#5 |
| M — `origin/feature/ml-finalize` | `416f24fcce459090580fdb9cb4a036c335621b14` | Новые ML-функции, ещё не в develop |
| U — `origin/feature/d09-d10-clarity` | `7c59dd0a7073ee0eb5bdee4f53599eacfe2bf221` | Актуальная UI-ветка, открытый PR #6 по контексту родительского исследования |
| `origin/feature/material-ingestion-bank-evidence` | `62e157b70d978f44fcbd77019acb7b99e2e0c454` | Уже объединённая D03/D04-ветка |
| `origin/feature/ml-insight-agent` | `7cbd690892addc495fbf9bee144c48931c464170` | Исходный ML; именно на него закреплена Java |
| Data-import implementation | `f44b69f0d124e8e4c33e5301b629413618090fa5` | Второй parent merge PR #2 `7cf5ec7` |
| Backend implementation | `5572d5af57779ed17bb38939b3325c3be71d5bce` | Второй parent merge PR #5 `5c41d77` |
| Исходный detached checkout / локальный develop | `cb28d5fa70ed0b3e86a4a3b5cd3788f1142eb2d8` | Не переключался; есть прежний untracked `docs/superpowers/` |
| Старый локальный materials worktree | `a3e5e149c973b6bdc88ef169f82c87587c1cf02c` | Старый собственный head, не актуальный origin head |
| main / origin/main / origin/HEAD | `6d49ff24767183c4130eb7f73bb41a3dc53df78f` | Старый начальный README, не MVP |

Во время аудита отдельный DevOps-чат продвинул локальную `feature/full-stack-compose` до `61632aea5ffb453240382bfab1d4f1d808b4b000`. Это только наблюдение ref; файлы этой работы здесь не проверялись и не изменялись. DevOps владеет root Compose/infra, а не D03/D04-планирование.

Удалённые feature refs data-import/backend-api/d09-d10-demo в актуальном `for-each-ref` уже отсутствуют. Их реализация не потеряна: import/backend доступны в merge parents; UI demo — в истории clarity. Не использовать старые ref names как существующие команды без проверки.

**Топология требует осторожности.** Между D и M два merge-base: `e5da8cce1aa364c47162e58af0485bd1cc8ad593` и `a3e5e149c973b6bdc88ef169f82c87587c1cf02c`. Для оценки реально отличающихся файлов использован двухточечный `git diff origin/develop origin/feature/ml-finalize`. Triple-dot с одним выбранным merge-base может ошибочно представить уже объединённые материалы как новые изменения.

Двухточечный diff всего дерева D→M: 98 файлов, +3257/−5757; **M не содержит почти всего Java Backend и актуального data-import**, поскольку её merge `416f24f` подтягивал старый develop `e5da8cc`, до PR #2–#5. Из этого нельзя заключать, что обычный merge удалит Java/import. Нельзя лишь заменять актуальный develop деревом M или переносить весь snapshot целиком.

**Безопасная симуляция обычного Git merge выполнена:** `git merge-tree --write-tree D M` создала временный tree `38fd3aec7d7d32fedf41155c0ff1403f9f08f0c4` и вернула ровно один конфликт — `.gitignore` (пустая строка возле повторённого CSV-ignore раздела). Checkout/index/refs не менялись; появились только Git objects. Проверки `git diff --exit-code` подтвердили: `backend-api`, `data-import`, `materials/*`, `tools/bank.py`, `agent/state.py`, `ml-service/config` в результате **побайтно равны D**; весь `ml-service` и `contracts` **побайтно равны M**. Итого D→временный merge tree — 49 файлов, +3250/−97, без удаления Java/import. Конфликтный `.gitignore` должен сохранить exclusions секретов, локального корпуса, review и CSV; достаточно убрать conflict markers/лишнюю пустую строку и при желании повтор CSV-правила.

**Предпочтительный путь — полный merge актуальной teammate ML-ветки** с разрешением этого малого конфликта и проверкой итогового дерева, а не повторное ручное копирование или cherry-pick взаимозависимых ML-коммитов. Семантические contract-правки из раздела 4 всё равно нужны: отсутствие текстовых конфликтов не подтверждает API-совместимость.

Для D+U также выполнен `git merge-tree --write-tree`: временный tree `3447d667142923fd2c8d34115c6f857ccc632c22`, единственный конфликт — add/add `frontend/README.md`. Exit-0 diff подтверждает, что backend/import/ML/contracts/infra побайтно сохранены из D, весь frontend кроме конфликтной README равен U. Предпочтение полной актуальной README UI с внесением согласованного real-runtime раздела; продуктовый UI-код переносится целиком. Симуляция не является фактическим merge или завершённым full-stack тестом.

В пределах `ml-service` + `contracts` D→M: 47 файлов, +3243/−95. `materials/*`, `tools/bank.py`, `agent/state.py`, `config/bank-documents.json` и прежний live-acceptance JSON **побайтно одинаковы** между D и M (`git diff --exit-code` вернул 0).

## 3. Что переиспользовать из новой ML-ветки

| Изменения M относительно D | Коммиты и файлы | Полезное поведение / ограничения |
| --- | --- | --- |
| Decimal для денег, разбор чисел с тыс/млн/млрд | `44a74e6`; `schemas.py`, `tools/profile.py`, `agent/critic.py` | Сохраняет денежную точность; требуется синхронизация Java-копий схем, поскольку Decimal разрешает JSON number/string |
| Более явный результат анализа | `66b8481`; `schemas.py`, `agent/orchestrator.py` | `outcome=full/limited/refused/none`, `error_code`, `claim_id`, version и request/run связи; Java сегодня их отбрасывает |
| Рублёвый budget и deadline | `2986799`; `budget.py`, `llm.py`, `config.py` | Полезная общая защита D11; тарифы по умолчанию/локальный ledger не подтверждают остаток общего ключа |
| Повтор запросов, версия пайплайна, сохранение инсайта | `333c76b`; `agent/store.py`, `agent/versions.py`, `agent/orchestrator.py` | Инсайт и evidence state сохраняются; request-id cache находится в памяти процесса и не равен долговечной идемпотентности после рестарта |
| Временной срез и защита prompts | `76110c2`; schemas, orchestrator, prompts, `tools/news.py` | `as_of`; future event отвергается, related news фильтруются по not_after; D03 bank policy сохраняется |
| Более аккуратная семантика профиля | `b1e9c18`, `5c7cfee`, `b508825`; `tools/profile.py`, `schemas.py`, tests | `observed→known`, сохраняются 0/999, ФТС не выдаётся за импорт/экспорт, self transfers не становятся контрагентом; исходные counterparties[i] сохраняются после сортировки |
| Офлайн-сценарии и D05 runner | `fce5e61`, `83962c7`; `evaluation.py`, `tests/test_scenarios.py`, `tests/test_evaluation.py` | Переиспользовать manifest/метрики совместно с D05; тестовые сценарии и scripted answers не подтверждают смысловое качество живой модели |
| UI-card и clarification ML endpoints | `340ce49`; `frontend_adapter.py`, `api.py` | Блоки fact/open/bank/ai и темы есть. Это вспомогательная проекция; браузерная интеграция U ожидает публичный Java API, не прямой ML |
| Официальные открытые источники | `09c9724`, `5656218`, `d07aa46`, `845700c`; `research/*`, planner/orchestrator/critic | Профиль определяет запрос ставки/FX/релизов CBR; роли analyst/planner/researcher/personalizer/verifier в trace; проверяются current-vs-prior ставки/FX |

### Граница с D04

В M `research/fetch.py:6–7,20–47` разрешает только `cbr.ru` / `www.cbr.ru`, HTTPS, до 2 MiB и проверяет redirects. `research/service.py:25–28,85–143` использует CBR RSS, ставку и FX, TTL-cache; поиск релизов ограничен текущей RSS-лентой и заголовочными stems. `research/plan.py:59–86` выбирает каналы по профилю, а не по произвольной модели.

Это **не независимый мульти-источниковый news ingestion и не замена текущему `materials/news.py`**. `orchestrator.py:126–131,157–170` превращает найденные документы в временные NewsEvent, регистрирует тексты в NewsStore, затем включает их в evidence state. `tools/news.py:39–41` прямо указывает, что зарегистрированный текст не попадает в corpus search. `agent/store.py:21–35` сохраняет state конкретного инсайта, но не публикует эти документы в MaterialStore/source_versions/corpus revision. Следовательно, lifecycle, дедуп/исправления и воспроизводимый news corpus нужно расширять через существующий слой материалов, согласовав общий интерфейс с ML.

Положительные ограничения сохранить: официальные данные остаются `kind=news`, отдельны от `bank`; hash содержимого создаётся; QA может восстановить evidence state после рестарта через `InsightStore`. Недоказанные будущие свойства: несколько независимых издателей, полная историческая лента, публикация динамических результатов в corpus, общая редакционная политика и полнота topical coverage.

## 4. Реальные расхождения контрактов

### 4.1. Java → ML: теряется неопределённость и управление запуском

Факт: D:`backend-api/src/main/java/ru/businessinsights/backend/ml/MlContracts.java:17` закрепляет `7cbd690`; `CompanyProfile:49–64` не имеет `revenue_year`, `revenue_status`, `revenue_scope`, `foreign_trade_data_available`, `payment_period`, `profile_quality_notes`, хотя эти поля присутствуют уже в D03/D04 и M:`schemas.py:75–120`. Java хранит часть provenance в своём profile snapshot, но ML его не получает. `InsightRequest:85–86` содержит только profile/role/event; `InsightService.java:267` не передаёт ML `request_id`, `parent_run_id`, `as_of`. Идемпотентность публичной Java-операции при этом уже есть — её не надо дублировать новым отдельным сервисом.

`NewsEvent:75–80` также не имеет `conflicts`; `SourceInput`/`mlSource` передаёт лишь id/title/date/page/url. D04-конфликт нескольких версий не доходит до ML по этому маршруту. Интеграционная приёмка должна доказать оба варианта: event из опубликованного corpus и event, зарегистрированный через Java API. Содержание одинаковых source IDs нельзя молча подменять.

### 4.2. ML → Java → UI: доказательства и исход анализа отбрасываются

Факт: D:`MlContracts.java:69–71` описывает SourceRef только как source_id/kind/title/date/page/url. В M:`schemas.py:123–132` дополнительно есть `doc_id`, `content_sha256`, `excerpt`. Java `HttpMlClient.java:82–93` валидирует JSON, затем преобразует его в DTO с `@JsonIgnoreProperties(ignoreUnknown=true)`. `InsightService.java:276` сохраняет уже преобразованный result. Поэтому новые поля источника не просто скрыты в UI — они удаляются из сохранённого Java result. `InsightMapper.java:75–80` и публичный `ApiModels.SourceView:88–89` их не предоставляют.

Нужно уточнить допустимый контракт provenance: `fragment_id` не является отдельным полем SourceRef; opaque source_id банк-фрагмента уже играет эту роль. Corpus revision/tool versions остаются в trace, а doc/hash/excerpt — поля SourceRef. Не обещать UI поля, которых реальная модель не экспортирует.

`MlContracts.Insight:139–146`, `Claim:95–99`, `ApiModels.InsightView:102–110`, `ClaimView:91–93` не сохраняют outcome/error_code/request_id/parent_run_id/cache_hit/version/claim_id. Старые JSON Schema не имеют `additionalProperties=false`; наличие новых полей само по себе не обязано вызвать rejection. Основной наблюдаемый дефект здесь — **молчаливая потеря информации**. M `outcome=refused` сейчас станет обычным Java LIMITED, и UI не сможет отличить обоснованный ограниченный результат от отказа без сохранения нового поля.

### 4.3. Согласование профиля уже частично сделано, документы и предупреждения отстали

M:`tests/test_backend_contract.py:11–58` воспроизводит допустимый Java-набор признаков; `b508825` исправляет counterparties indices. Это полезная регрессия, но тест исполняет Python-копию правила, а не настоящий Java-клиент. Его нельзя считать доказательством cross-language round trip.

D:`InsightService.java:311–327` всё ещё предупреждает, что ML отбрасывает 0..999, считает группу по выручке ЮЛ, называет участников покупателем/поставщиком, меняет индексы и выводит импорт/экспорт из ФТС. Часть этого исправлена даже до M, остальное — в M. `processAnalysis:268–272` превращает `complete` в `LIMITED` при любом таком warning. Нужно заменить устаревшие совместимые предупреждения проверенными quality notes и обновить закреплённые SHA, не удалить реальные оговорки о неизвестной выручке группы/договорах/направлении торговли.

### 4.4. UI имеет транспорт, но DTO и авторизация пока не совпадают

U:`frontend/web/integration.js:65–72` требует семь реальных bindings: bootstrap/feed/insight/clarify/preferences/savePreference/opened. Файла их реализации в U нет; `createClient` по умолчанию возвращает demo. Существующий `server.py:169–206` обслуживает синтетические `/api/bootstrap`, `/api/feed`, `/api/insight`, `/api/clarifications`.

Java API D:`BackendController.java` использует `/api/v1/session`, `/companies`, `/companies/{id}/profile`, `/analyses`, `/operations/{id}`, `/companies/{id}/insights`, `/insights/{id}`, `/questions`, `/answers/{id}`, `/state`, `/opened`, `/feedback`. Bootstrap-агрегатора и чтения feedback preferences нет. Java feed — список **уже подготовленных инсайтов**, а UI feed model — список событий с context. UI insight_id может быть opaque ML ID в демо/ML-проекции, Java публичный insightId — UUID операции. Binding должен явным образом сопоставлять эти сущности и UUID, не разбивать произвольные строки по `:`.

U транспорт посылает same-origin cookies, Idempotency-Key, опрашивает Location/Retry-After и не повторяет POST (`integration.js:27–58`). Java demo auth требует `Authorization: Bearer …` (`DemoAuthFilter.java:37–51`). Без согласованного same-origin proxy/session механизма все эти browser requests получат 401. Токен не следует добавлять в localStorage или статический JS. Реализация proxy/доставки UI принадлежит DevOps/Backend, не D03/D04.

UI читает ошибки как `data.error.message` (`integration.js:44`), Java возвращает плоский ProblemView `{status,code,message,retryable,requestId}` (`ApiModels.java:77`, `DemoAuthFilter.java:56–60`). Нужен явный перевод ошибки. UI certainty/outcome vocabulary (`COMPLETE/LIMITED/INSUFFICIENT_DATA/NO_RESULT/FAILED`) отличается и от Java operation (`SUCCEEDED`, …), и от M outcome (`full/limited/refused/none`). Нужно согласовать одну таблицу семантического отображения, отдельно для analysis/question, включая unknown status.

### 4.5. Источник PDF: готовые части и оставшаяся цепочка

D03 API уже отдаёт оригинальный PDF; M/D `api.py:24–35` формирует ссылку через `materials_public_base_url` и `/v1/materials/documents/{doc_id}/file#page=N`. U:`integration.js:5–17` умеет безопасный http/https URL, opaque IDs и `#page=N`; `FE_08_13_PREPARATION.md:59` описывает excerpt/claim_id/feature refs. Это переиспользовать.

Нерешённый маршрут должен связать browser origin → разрешённый PDF URL → конкретный document/revision → исходные bytes. При пустом public base ссылка относительна `/v1/materials/…`; Java `InsightMapper.safeUrl:100–106` допускает только абсолютный http/https host и превратит её в null. В контейнерной сети `http://ml:8000` также не является браузерным адресом. Конкретный public base/proxy/auth должен определить DevOps/Backend; D03/D04 отвечает за корректные doc/hash/page/excerpt и совпадение bytes с manifest. Принимать навигацию нужно в реальном браузере и по реальному PDF, включая страницу, а не только по URL unit-тесту.

M:`frontend_adapter.py:21–30,60–62` объединяет несколько claims в одну строку раздела и оставляет только объединённые source IDs/certainty; source_view опускает doc/hash/excerpt, section опускает claim_id/profile_features. Его не стоит назначать окончательным evidence API без доработки. Предпочтительна единая согласованная проекция Java→UI с массивами claims и происхождением, чтобы не поддерживать два расходящихся адаптера.

## 5. Проверки, реально выполненные 26.10

Все результаты ниже получены без live LLM и без публичного HTTP-поиска. Для Python использован `/Users/selebry/Documents/dev/alpha-hack/.worktrees/business-insights-materials/.venv/bin/python`, PYTHONPATH указывал на src соответствующего frozen snapshot, `PYTHONDONTWRITEBYTECODE=1`; pytest cache отключён. Snapshot не перестраивался, окружение не менялось.

| Проверка | Результат | Что это доказывает / чего не доказывает |
| --- | --- | --- |
| D: полный ML `pytest -q -p no:cacheprovider` | **115 passed**, 0.90 s | Нынешняя materials/ML-регрессия проходит в существующем окружении |
| M: полный ML, та же команда | **242 passed**, 1.61 s | Новые функции и старые материалы проходят вместе в Python |
| D: data-import `unittest discover -s tests -p 'test_*.py' -v` | **6 passed, 1 skipped** | Unit-конверсия/качество; PostgreSQL test пропущен: disposable DSN не задан |
| U: frontend `unittest discover -s tests -p 'test_*.py' -v` из `frontend/` | **20 passed** | Синтетический HTTP-контракт UI |
| U: `node tests/test_frontend.cjs` | **PASS** | Контекст/DOM/источники/feedback/races на демо |
| U: `node tests/test_integration.cjs` | **PASS** | Безопасность URL, PDF page fragment, polling/Retry-After, idempotency, abort, access states и server-mode mock bindings |
| Java automated tests | **Не обнаружены** | В D нет `backend-api/src/test`, pom не содержит test dependency; build/run/DB в этом исследовании не выполнялись |
| Git diff materials invariance | **exit 0** | Перечисленные D03/D04 файлы сохранены в M; это не проверка реального runtime corpus |

В обоих ML suites один upstream Starlette/TestClient/httpx deprecation warning. Первые UI-команды из корня snapshot не нашли `server`/`python`; затем запуск исправлен: рабочая папка `frontend/`, существующая venv/bin в PATH. Итоговые PASS выше относятся к исправленному штатному запуску; зависимость от рабочего каталога стоит сохранить в инструкции.

Повторный Docker/full-stack/браузерный/live LLM прогон, проверка реального корпуса, актуальные тарифы/остаток общего ключа и ручное semantic scoring в рамках этого branch-аудита **не выполнялись**. Исторические цифры `docs/materials-acceptance.md` (9 PDF, 11 verified, 12 news, 10/10 gold, 5 limited/1 failed) остаются записью 24.10, а не новой приёмкой. A10 в этом документе прямо не принят.

## 6. Приоритетный backlog и границы ответственности

Статусы ниже — предлагаемый план для согласования, а не уже выполненные изменения. D03/D04 принадлежит владельцу материалов; D02/D11 — владельцу ML; D05/D06/D08 — Backend/оценка; D09/D10 — UI; root Compose/infra — действующему отдельному DevOps-чату.

| Приоритет / задача | Владелец и конкретные пути | Готовый проверяемый результат | Зависимость |
| --- | --- | --- | --- |
| **P0. Согласовать сохраняемый контракт** | Backend + ML + D03/D04; `contracts/ml/*`, Java `MlContracts.java`, resource `ml-contracts/*`, `ApiModels.java`, `InsightMapper.java` | Doc/hash/excerpt, opaque source IDs, claim_id, conflicts, profile uncertainty, outcome/error/request/time survive Java request/response/storage; synthetic round-trip и реальные schemas | До UI mapping и совместной приёмки |
| **P0. Объединить полную M с D** | ML-владелец / интегратор; полный merge, `.gitignore` | Разрешён единственный текстовый конфликт; Java/import/materials сохранены как в проверенной merge-tree симуляции; все 242 ML-регрессии, согласованные contract fixtures | До end-to-end запуска |
| **P0. Зафиксировать news→Java→ML путь** | D04 + Backend + ML; `materials/cli.py`, `materials/store.py`, `schemas.py`, Java EventInput/InsightService | Один опубликованный event с body, source IDs/hash, published_at/retrieved_at/version/conflicts передаётся в анализ; correction/duplicate/as_of не теряются | Contract agreement |
| **P0. Соединить browser API и источники** | Backend/UI + DevOps; Java OpenAPI/controller, UI `web/integration.js` + новый согласованный bindings/config, public URL/proxy | Никаких mock bindings в server mode; session/access, async analyse/question, errors/outcomes, canonical UUID, PDF 200 и #page работают в браузере | M integration + DevOps route |
| **P0. Убрать устаревшие compatibility assumptions** | Backend + ML; `InsightService.java:311–327`, `docs/ml-compatibility.md`, M `docs/integration.md` | SHA и warnings описывают фактическую ML-версию; complete не становится limited от уже исправленного дефекта; реальные missing-data сохраняются | Выбранный ML head |
| **P1. Расширить D04 источники через существующий MaterialStore** | D04; `materials/news.py`, `sources.py`, `dedup.py`, CLI/store; интерфейс с `research/*` согласовать с ML | Минимальный согласованный набор независимых первичных издателей, immutable raw/version/hash/timestamps/original URL, same-event multi-source, correction/late import/idempotence/failure rollback; allowlist/fixtures | Новые publisher requirements и contract |
| **P1. Завершить D03 покрытие и provenance** | D03; `config/bank-documents.json`, review/gold, `materials/bank_corpus.py`, API | Проверенный актуальный manifest; достаточные verified statements для выбранных demo тем, holdout, no-coverage и stale/future/superseded; PDF bytes/page/excerpt chain | Не расширять verified механически без visual review |
| **P1. A10: совместная смысловая приёмка** | ML + D03/D04 + D05 | Фиксированные новые event×profile случаи, разметка claims по смысловому следованию и персонализации; ошибки эмитент/участник, copy/weak company linkage и потерянные разделы закрыты | Offline first, live runs только с согласованными budget/model/stop rules |
| **P1. Java/DB/integration проверки** | Backend + DevOps + D05 | Test fixtures запускаются против настоящего Java клиента и disposable PostgreSQL; schema/profile/event/answer/source/operation round trip; restart + trace + ownership checks | Compose и auth route |
| **P2. Уточнить lifecycle гарантий ML** | ML + Backend | Документировать, что request_id cache in-process, persisted insight ≠ persisted request idempotence; решить необходимость долговечного/shared dedup, concurrency budget/research TTL и time precision | Требования запуска/числа workers |
| **P2. Обновить README/handoff** | Каждый владелец своего модуля | Одна актуальная таблица SHA/contract/version, команда запуска из правильной папки, known limits, reproducible acceptance manifest | После выбора integrated head |

**Предлагаемый порядок:** сначала согласовать контракт и выполнить полный merge M с D (единственный `.gitignore` конфликт); затем полный merge U с разрешением `frontend/README.md`. Параллельно владельцы Backend/UI согласуют bindings и статус/ID/error mapping, DevOps готовит маршруты. Затем один офлайн/fixture полный путь материалов → Java → ML → Java storage → UI → source PDF → QA/trace/feedback. После него — увеличение D04 источников и D03 проверенного покрытия, отдельная смысловая оценка новых событий/профилей, затем ограниченный согласованный live acceptance. Больше publisher adapters само по себе не закрывает потери contract/provenance. Не повторять ML-функции вручную: современный Python source в симуляции полностью совпадает с M и уже сохраняет D03/D04.

Границу не расширять незаметно: владелец D03/D04 не переписывает Java/JS/агентный pipeline/root Compose ради самостоятельной демонстрации. Он готовит материалы, fixtures, JSON Schema предложения и критерии, а изменения этих компонентов реализуются с их владельцами. Уже работающий D03/D04 код не переносить заново из stale worktree и не создавать новый монолит поверх нынешних сервисов.

## 7. Критерии завершения D03/D04 после интеграции

1. Manifest фиксирует документ/новость, SHA, даты публикации/получения, версию extraction/OCR/review и corpus revision; повтор тех же inputs не меняет публикацию.
2. Один claim в карточке и уточнении разрешается через неизменённый source_id к doc/hash/excerpt и реальной странице PDF либо оригинальной статье; ревизии и конфликтные версии не исчезают в Java storage.
3. Отсутствие/устаревание/будущее/неподтверждённый OCR/расхождение источников даёт наблюдаемое ограничение/отказ и не превращается в «позицию банка».
4. Новости выбранного реального независимого publisher сохраняются тем же lifecycle, а request-time CBR enrichment не выдаётся за общий news ingest.
5. Пройдены подходящие offline/cross-language/DB/browser проверки на одном зафиксированном integrated head. Исторические проверки не подменяют их.
6. A10 принят по ручной смысловой рубрике на новых event×profile случаях; до этого итоговый MVP не объявляется полностью завершённым. Live budget/API остаток подтверждается отдельно владельцем общего ключа.

## 8. Воспроизводимость и практические риски

Git-факты получены через `for-each-ref`, `log`, `merge-base --all`, двухточечные `diff` и безопасную `merge-tree --write-tree` симуляцию без изменения checkout/index/refs и без настоящего merge. Проверки SHA на момент исследования воспроизводимы через `git show <SHA>:<path>`. Строки выше относятся к frozen snapshots; после интеграции они могут сдвинуться.

Главные риски по убыванию: молчаливая потеря evidence/outcome в Java; несовпадающий browser auth/DTO; подмена актуального develop старым ML деревом; ложная готовность от 242 scripted tests; узкое topical покрытие всего 11 reviewed statements; CBR-only enrichment, ошибочно принимаемый за диверсифицированный news corpus. Совместный DevOps runtime, полный корпус и team handoff проверяются отдельными работами и не объявляются завершёнными этим отчётом.

## 9. Дополнение: исправление новости, очередь, обновление корпуса и старый QA

Это уточнение выполнено 26.10 по тем же D/M SHA, без реализации. Оно конкретизирует P0 «news→Java→ML», чтобы исправление D04 не ломало регистрацию и не меняло вход уже созданной операции.

### Наблюдаемое поведение

| Этап | Точный источник | Реальное следствие |
| --- | --- | --- |
| D04 correction | M/D `materials/dedup.py:74–94`, `models.py:59–70` | Исправление прежнего URL сохраняет **логический event_id**, создаёт новый immutable source_id `news:…:rN:hash`, старую SourceVersion оставляет неактивной. `events_from_versions:33–52` пересобирает текущие body/title/source refs/conflicts |
| Java регистрация | D `OperationStore.java:41–54`, `InsightService.java:92–104`, `V1__backend.sql:2–8` | Единственная строка на event_id; INSERT ON CONFLICT DO NOTHING и сравнение полного canonical payload hash. Изменённый body/title/date, добавленный related source или иной payload того же ID → **409 EVENT_ID_CONFLICT**. Это несовместимо с нормальным D04 correction lifecycle |
| Создание операции | D `InsightService.java:106–115`, `OperationStore.java:58–74`, `ApiModels.java:42–45` | До enqueue проверяется наличие события, но в operation.payload остаются companyId/role/eventId/userProvided, а не его версия/body. Fingerprint этой операции не содержит event revision, corpus revision, profile dataset SHA или as_of |
| Исполнение очереди | D `InsightService.java:250–267` | Worker заново читает текущий профиль и `store.event(op.eventId())`, только затем сохраняет profile snapshot/event. **Сегодня событие не дрейфует**, потому что Java запрещает его менять. Профиль между submit и execute может обновиться. Если просто заменить event INSERT на UPSERT, появится race: уже поставленная операция незаметно возьмёт исправленное событие |
| Новый ML run | M `api.py:24–35`, `orchestrator.py:140–170,271–302`, `schemas.py:178–197` | Bank/news берутся из snapshot, загруженного **текущим ML-процессом при старте**. В request нет expected corpus revision. Переключение `current.json` без restart не обновляет этот процесс; другой/перезапущенный процесс может использовать новый corpus. Trace пишет фактическую revision, но Java заранее её не закрепляет |
| Старая запись и QA | M `agent/store.py:21–35,42–68`, `orchestrator.py:231–235`, `agent/qa.py:20–33` | Старый request, retrieved bank/news и resolved texts действительно восстанавливаются с диска. Затем QA копирует state и вызывает `agent.tools_for`, чьи closures ищут в **agent.bank/agent.news нового процесса**, а не в историческом corpus родительского run. Это добавляет новые основания к старому QA |
| Временная граница | M `orchestrator.py:140–155`, `materials/policy.py:7–20`, `tools/news.py:50–59` | Publication-date/as_of фильтры не означают «известно на момент исходного запуска»: исправленная позже статья с прежней published_at всё равно может пройти. При as_of=None related-news вообще не имеет верхней даты. Bank window для датированного события заканчивается датой события, независимо от более позднего request.as_of |
| Feed и результаты | D `OperationStore.java:92–105,112–119`, `InsightService.java:134–145` | Feed содержит только ANALYSIS с готовым SUCCEEDED/LIMITED result. Новая регистрация новости сама не создаёт анализ и не попадает в этот feed. Correction/new analysis создаст отдельную операцию, старые result/feedback не переписываются автоматически |

**Офлайн-воспроизведение QA drift.** В TemporaryDirectory создан инсайт на синтетических штатных fixtures M и ScriptedClient; затем агент восстановлен с тем же хранилищем инсайтов, но bank index содержал ту же допустимую по датам запись под новым `fragment_id=new_corpus_fragment`. У старого инсайта этого source ID нет. QA после восстановления выполнил `search_bank_analytics`, нашёл новый ID и вернул `status=answered` с ним в sources. Никаких сетевых/LLM запросов не было. Это доказывает расширение основания QA из текущего индекса; **сам сохранённый родительский result при этом не мутирует**. Существующий `tests/test_idempotency.py:85–99` проверяет restart с прежними fixtures, но не смену corpus.

Дополнительная граница оригиналов: `materials/store.py:134–157` умеет читать immutable snapshot по revision внутренним helper, но публичный load читает current pointer; PDF router `materials/api.py:10–19` закреплён на snapshot процесса. При удалении старого doc_id из нового snapshot старый URL может вернуть404, хотя исходные bytes остаются в `raw/<hash>/source.pdf`. Doc IDs и raw paths действительно hash-bound (`bank_corpus.py:21–23,71–75`). Нужен разрешённый URL с corpus revision либо resolver по историческому immutable doc registry, а не обход registry ради доступа к raw path.

### Минимальный безопасный handshake для MVP

**Предлагаемый ограниченный вариант:** оставить историю материалов в уже существующих immutable D04 snapshots; в Java иметь текущий каталог и полностью замороженный вход каждой операции. Новая отдельная таблица всех event revisions не обязательна, пока не требуется произвольный historical browse/replay без существующей операции. Простой UPSERT без закрепления операции небезопасен.

1. **D04 публикует versioned envelope через доверенный внутренний ingest**, а не через браузер. Минимальные поля: `event` с прежним логическим event_id и расширенными source/conflict полями; `event_revision` = hash согласованного canonical event payload; `corpus_revision`; immutable `source_version_ids`; publication timestamp; ожидаемая предыдущая catalog publication/revision. `retrieved_at` и chronology сохраняются в source metadata, не заменяются временем Java POST. Без изменений event payload повторная доставка идемпотентна; одна event revision может входить в несколько corpus revisions. Backend проверяет hash, refs и compare-and-swap текущей catalog publication, чтобы поздний batch не откатил head. Текущий публичный append-only POST нельзя незаметно превратить в свободный update: отдельная internal boundary или проверенный import consumer должны иметь полномочия publisher.
2. **Java расширяет текущую event row и атомарно фиксирует operation input при submit.** Добавить revision/catalog metadata к существующей `backend_api.event`, а в `operation.payload` сохранять `FrozenAnalysisInput`: полный event envelope, profile snapshot + dataset SHA, role/userProvided, as_of, expected corpus revision, выбранные версии ML contract/pipeline. Чтение catalog/profile и запись операции — согласованный transaction snapshot; worker читает только FrozenAnalysisInput, без повторного `store.event` и загрузки текущего профиля. Это узкая миграция/DTO-поправка в `V2__…sql`, OperationStore/InsightService/ApiModels, без переписывания import schema и новой очереди.
3. **Revision входит в запрос анализа, но повтор старой операции не переосмысляется.** Каталог отдаёт event_revision/corpus_revision; новый AnalysisInput явно выбирает их. Повтор того же Idempotency-Key и того же публичного input возвращает прежнюю операцию с её frozen inputs, даже после обновления current catalog; lookup существующего ключа нужен до разрешения нового current head. Тот же ключ с другим намерением →409. Исправление/пересчёт — новая operation + новый ключ + checked `supersedes/retry_of` ссылка на прежнюю operation; старые result/source IDs/feedback остаются. После timeout неизвестный результат не запускается автоматически повторно; восстановление/новый платный run требует отдельного явно наблюдаемого действия, поскольку долговечного ML request-id lookup сейчас нет.
4. **ML проверяет expected corpus revision до LLM и сохраняет её вместе с state/result.** Добавить revision в InsightRequest и request fingerprint, result/trace/InsightStore envelope. Для минимального deployment агент обслуживает только свой загруженный snapshot; несовпадение expected/loaded revision → явный `409 CORPUS_REVISION_MISMATCH`/согласованный код, без silent latest и без платного вызова. Поддержка нескольких старых snapshots — отдельное расширение, а не обещание MVP. D03/D04 publication → restart/health revision → catalog promotion должны составлять явный rollout: новые задания не направляются на процесс со старой revision.
5. **Старый QA по умолчанию отвечает только на сохранённых evidence.** Минимальная правка `agent/qa.py` — не вызывать current global search для старого инсайта, использовать сохранённые bank/news/resolved texts и явно отвечать no_answer при нехватке. «Обновить анализ» создаёт новую operation с новой revision, не меняет смысл старого вопроса. Если дополнительный поиск нужен именно в старом corpus, tools должны строиться из явно загруженного parent corpus revision с теми же date/policy constraints; одного восстановления уже retrieved subset недостаточно для полного исторического поиска. PDF/source links также должны разрешаться по revision, сохранённой у parent run.

**Когда оправдана отдельная таблица `event_revision`.** Если продукту нужен каталог всех исправлений, старые неопубликованные в operation версии, произвольный replay или SQL-доступ к истории без D04 storage, добавить immutable `(event_id,event_revision,payload,source_refs,corpus_revision,published/observed_at)` и current-head pointer. Это предпочтительнее дальнейшего усложнения JSON current row для таких требований. Внешний handshake и FrozenAnalysisInput остаются теми же. Хранить историю только в mutable event row и надеяться на прежние result JSON нельзя.

### Конечный путь «новость → анализ → лента» и проверка correction/retry

До первого нового источника Backend/ML/D04/UI согласуют envelope, revision enforcement и один publisher consumer. Затем реализация ограничивается следующей последовательностью:

1. D04 ingests новую статью, валидирует/publishes immutable corpus R1 и export envelope E1/V1. DevOps/ML активируют R1 и подтверждают `/health.corpus_revision`; Backend consumer публикует E1/V1 идемпотентно.
2. Backend отдаёт каталог доступных событий с revision (минимальный согласованный `GET /api/v1/events` или эквивалентный bootstrap/feed endpoint). Текущий `/companies/{id}/insights` остаётся списком готовых результатов; UI binding должен явно различать «новое событие, анализа ещё нет» и готовую карточку. Сам ingest не создаёт неограниченный LLM fan-out по всем клиентам: выбирается конечный список company×role или явный пользовательский анализ с бюджетной границей.
3. Submit analysis E1/V1/R1 атомарно сохраняет FrozenAnalysisInput. Java возвращает202/Location, worker передаёт frozen data и operation-derived request_id в ML, проверяет corpus agreement, сохраняет result/provenance и actual revision. После готовности существующий feed показывает этот результат, источник/QA/trace/feedback связаны с его operation UUID.
4. Для исправления D04 публикует E1/**V2**/R2 с тем же логическим event_id и новыми source version IDs. Internal catalog CAS принимает новую публикацию; прежние operations остаются E1/V1/R1. После активации R2 новый analysis с новым ключом создаёт отдельный result, отмеченный как superseding прежний. Feed либо явно показывает обе версии, либо выбирает последнюю по согласованной политике с доступной историей; нельзя незаметно заменить карточку, к которой уже привязана оценка.
5. Контрольные сценарии: (a) повтор доставки V1 не меняет catalog; (b) V2 принимается внутренним ingest при том же event_id; (c) queued V1 выполняется на V1/R1 либо явно останавливается при недоступном R1; (d) тот же request key после refresh возвращает прежнюю operation; (e) новый ключ/V2 даёт новый результат; (f) старый QA после restart не использует source IDs из R2; (g) старый PDF доступен по hash/revision; (h) несогласованный corpus/hash или stale publication отвергается до LLM. Проверять это synthetic cross-language/DB tests и browser chain, затем отдельной ограниченной live-приёмкой.

Приоритет этого дополнения — **P0 до разрешения corrections/upsert и до демонстрации новой новости через Java**. Владельцы: D04 — envelope/export/immutable history, Backend — catalog/CAS/frozen operation/feeds, ML — revision guard/state/QA policy, UI — status/version mapping, DevOps — snapshot activation/route; оригинальный full-merge вывод раздела2 остаётся прежним.
