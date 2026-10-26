# D03/D04 Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task. Пользователь выбрал работу по чатам и субагентам; file ownership ниже обязателен. Исследование закончено; исполнение этого нового плана — после его согласования.

**Goal:** довести материалы и банковскую экспертизу до трёх реальных издателей и принятого сквозного сценария без потери фактов, версий и evidence.

**Architecture:** расширить существующий materials/ML и аккуратно объединить teammate ML. Один coordinator владеет общими models/contracts/runtime; publisher adapters и review банковских PDF выполняются параллельно. Java/UI/DevOps подключаются через явный catalog/revision/provenance контракт.

**Tech Stack:** существующие Python3.12/FastAPI/Pydantic/httpx/BeautifulSoup/pypdf/Poppler/Tesseract, Java/Spring/PostgreSQL17+, текущий Python/vanilla-JS frontend, Docker Compose. Новые инфраструктурные сервисы не требуются.

**Spec:** [2026-10-26-domains-03-04-finalization-design.md](../specs/2026-10-26-domains-03-04-finalization-design.md). **Research:** [отчёты 00–03](../../research/2026-10-26/00-context-and-scope.md). **Workstreams/prompts:** [2026-10-26-finalization-workstreams.md](2026-10-26-finalization-workstreams.md).

**DevOps baseline:** PR #7, `b177c7dcdf3225a99c5e99c034e5758762d28649`, уже включает UI `7c59dd0` и `frontend/web/backend-bindings.js`. Предыдущий UI merge не повторять. Его smoke синтетический.

**Статус:** план для согласования; указанные задачи/thresholds не являются выполненными проверками. Отдельный DevOps чат уже исполняет собственное поручение пользователя, его работа не блокируется этим планом.

## Global Constraints

- Минимум Python 3.11 сохраняется; проверенный Docker runtime — 3.12. Не вводить 3.12-only синтаксис без явного изменения совместимости.
- Свежий baseline: develop e105b7c, ML416f24f, UI7c59dd0; перед исполнением fetch+сверка новых SHA, включая DevOps. Не reset исходный detached checkout и старый materials worktree.
- Полный проверенный merge ML сохраняет Backend/import/D03-D04; разрешить .gitignore. UI README add/add согласовать. Не копировать дерево старой ветки поверх develop, не сливать remote shared branches/чужие PR без команды.
- Три publishers в рекомендуемом объёме: CBR+FNS+EIA. Это предложение, не норматив кейса. MOEX только annotations/P1; EEC/прочие pending кандидаты не входят в обещанный результат.
- Разрешённые HTML paths/robots/terms/TLS соблюдаются. EIA RSS, FNS rss_region, paywall/challenge/credential обходы не используются. Registry IDs, не произвольные пользовательские URLs.
- Только6разрешённых генераторных моделей через выданный gateway. Сбор/parsing не вызывает LLM. Shared500руб/день/reset03:00МСК; live evaluation cap100руб и фактический остаток должны быть проверены отдельно.
- CBR indicators/research и persisted news разные режимы; public_analysis/indicator/annotation никогда не bank assessment. Даты публикации, наблюдения, effective date и точность времени различаются.
- Versioned sources/raw/pages не перезаписываются; v1 corpus169bef4… остаётся читаемым. Pending OCR не становится verified автоматически.
- Все новые поля проходят ML schema→Java DTO/JSONB→UI. Не терять массивы claims, SourceRef, conflicts, profile uncertainty, outcome и request/version context.
- Существующие242ML,20UI/Python и2Node suites сохраняются. Offline/fake runs не выдаются за real source/LLM/DB/browser acceptance. Data-import не переписывается.
- Общие models/CLI/registry/schema/agent state пишет один интегратор. Source/PDF субагенты меняют только выделенные файлы; Java/UI waits for committed DevOps baseline.

## Review Focus

1. HTTP200 с пустым feed/menu-only текстом, дата без timezone/времени, CSS-hidden архивная метка: не становятся точной свежей статьёй. Tests T02–T04.
2. Один publisher/одна статья fails во время mixed batch; параллельный bank importer и crash pointer: успешные записи/старые failed-source данные сохраняются, reports не маскируют деградацию. T05.
3. Same event correction+новый corpus, queued analysis и старое QA/PDF: стабильный ID, новый hash, frozen inputs и исходная revision не подменяются нынешним corpus. T07–T09.
4. Tax/refinery «rate», чужая география, flipped negation, эмитент вместо манипулятора, forecast→fact, EIA перевод: false claim не публикуется как подтверждённый. T03/T04/T06/T10.
5. Java ignoreUnknown, UUID vs opaque IDs, same-origin auth/PDF и разные outcome: данные не теряются в DTO/DB/UI, страница действительно открывается, отсутствие evidence видно. T08/T11/T12.

## Пути в задачах

Python package paths `materials/`, `agent/`, `research/`, `tools/`, `schemas.py`, `api.py`, `config.py`, `evaluation.py`, `export_schemas.py` отсчитываются от `ml-service/src/insight_agent/`. Пути `tests/`, `config/`, `eval/` — от `ml-service/`. Root `contracts/`, `docs/`, `frontend/`, `infra/` указаны относительно repo. Java alias J определён в T08.

## Порядок и gates

`T01 → T02 → (T03 || T04 || T06) → T05 → T07`.

`T08 → T09` выполняет один Java/integration lane после T01/T02 и фиксации DevOps baseline; T09 завершается после T07. T10 начинается по утверждённым контрактах T02/T06 и интегрируется после T07. `T11 → T12` требуют T05–T10 и общего запуска.

Зависимости проверяются по committed интерфейсам, не по сообщению «почти готово». Если baseline продвинулся, coordinator обновляет план/manifest и согласует changed interfaces до параллельного запуска.

### Task 1: Интеграционная база и release contract manifest

**Владелец:** coordinator, совместно с владельцами ML/DevOps. **Gate:** G01. **Файлы:** Modify `.gitignore`, `frontend/README.md`, актуальные integration docs; Create `contracts/materials/README.md`, `docs/finalization-baseline.json`.

**Интерфейсы:** baseline manifest хранит develop/ml/ui/devops SHA, schema/corpus policy versions, ownership. Это только совместимый integration head; никаких новых adapters в T01.

- [ ] Зафиксировать SHA/clean status, поднять isolated worktree от current develop; проверить remote PR2–6 и DevOps changes. Не использовать stale a3e5e14.
- [ ] Повторить non-checkout `merge-tree` для актуальных ML/UI. Выполнить merge в свою feature-ветку, сохранив exclusions/README, затем подключить согласованный DevOps commit. Если teammate PR уже merged — повторный перенос не делать.
- [ ] Run ML `python -m pytest ml-service -q`; expected не менее 242 PASS до новых тестов. UI из `frontend/`: `python -m unittest discover -s tests -v`, `node tests/test_frontend.cjs`, `node tests/test_integration.cjs`; ожидаются 20 PASS и три успешных Node-набора после PR7. Import6unit, PostgreSQL separately via disposable DB.
- [ ] Export schemas и сравнить с contracts. Manifest должен отличать ещё не обновлённые Java schemas от согласованных; старое предупреждение «Backend ещё нет» удалить.
- [ ] Commit `chore: establish integrated materials finalization baseline`. Не объявлять полный стек принятым по этому commit.

### Task 2: Metadata v2, совместимость и реестр источников

**Владелец:** координатор; запускается перед субагентами источников. **Gates:** G02/G03. **Файлы:** Modify `materials/models.py`, `materials/sources.py`, `materials/store.py`, `materials/dedup.py`, `schemas.py`, `export_schemas.py`, `research/fetch.py`; Create `materials/adapters/__init__.py`, `materials/registry.py`, `materials/transport.py`, `materials/legacy.py`, `config/public-sources.json`, `materials/identity.py`; tests `test_material_compatibility.py`, `test_source_policy.py`.

**Интерфейсы:**
- `CatalogToken(store_id:str,generation:int,revision:Sha256)`: отдельно от event hash; generation0 для legacy pointer, монотонное продвижение под lock.
- `DiscoveredArticle(url:str,title:str,raw_date:str|None)` и `NewsSourceAdapter.collect(limit:int)->FetchBatch` определены в sources.py; оба adapter агента используют эти имена.
- `event_content_sha256(event:NewsEvent)->Sha256` находится в identity.py: canonical JSON title/text/publication/precision/source IDs+hashes+URLs/topics/conflicts, без fetch/check time.
- `SourceSpec`: source_id/publisher_id/hosts/allowed_paths/discovery_url/parser_version/language/material_type/date_policy/rights_url/limits.
- `FetchBatch`: source_id, records:list[RawNews], status=`ok|partial|empty|unchanged|blocked|failed`, attempted/succeeded/rejected/item_errors, check/last-success metadata.
- `NewsSource.fetch(limit:int)->list[RawNews]` сохраняется для legacy; новый `collect(limit:int)->FetchBatch` обёрнут collector-ом. `get_source(source_id,transport)->NewsSourceAdapter` использует только fixed registry.
- `PublicationPrecision=timestamp|date|unknown`, `published_date:date|None`, `published_at:AwareDatetime|None`, raw publication и source-timezone provenance. material types из spec; `RawNews.topics:list[str]` и SourceVersion.topics содержат source-normalized topic tags; NewsEvent получает optional published_date/published_precision и tags из primary version, а source_ref сохраняет day-only date. `SourceRef` optional metadata расширяется без обязательности для старых запросов.
- `load_snapshot` проверяет v1 hash исходного canonical JSON до добавления новых defaults, затем валидирует и даёт совместимое представление; v2 publish не перезаписывает v1.

- [ ] RED-фикстуры: synthetic корректный v1 snapshot/hash, old saved batch без новых fields; дата без времени, время без зоны, timestamp с явным offset; неизвестный registry ID, redirect на чужой/private host, URL с credentials; source gate2MiB/timeout/Retry-After bounds. Assertions: v1revision сохранена, invented midnight отсутствует, forbidden request не отправлен.
- [ ] Run `pytest ml-service/tests/test_material_compatibility.py ml-service/tests/test_source_policy.py -q`; expected FAIL на отсутствующих v2/interfaces.
- [ ] Реализовать typed metadata/legacy adapter/registry, общий контролируемый transport; research SafeFetcher делегирует ему без переписывания indicators/TTL. Сохранить raw original body отдельно от normalization, атрибуцию и parser version; dynamic health не участвует в event identity.
- [ ] GREEN этих тестов + full ML; реальный локальный v1 corpus169bef4… загружается и bytes/hash не меняются. Export schema snapshots.
- [ ] Commit `feat(materials): add source policies and compatible v2 provenance`; выдать двум source агентам exact API/commit. До него не писать интеграции по догадке.

Первый тест T02 (legacy fixture создаётся до изменения models):

```python
def test_v1_hash_and_date_precision(legacy_store):
    old = load_snapshot(legacy_store.root)
    assert old.revision == legacy_store.expected_revision
    assert old.news[0].event_id == legacy_store.expected_event_id
    assert old.source_versions[0].content_sha256 == legacy_store.expected_source_hash
```

### Task 3: Реальный источник ФНС и tax classification

**Владелец:** D04 chat, отдельный FNS субагент. **Gates:** G02/G03. **Файлы:** Create `materials/adapters/fns.py`, `tests/test_fns_source.py`, небольшие фикстуры `fixtures/materials/fns-*`; registry merge выполняет coordinator.

**Интерфейсы:** `FnsNewsSource.collect(limit:int)->FetchBatch` через transport/SourceSpec T02; `parse_fns_index(html:bytes,base_url:str)->list[DiscoveredArticle]`; `parse_fns_article(html:bytes,url:str)->RawNews`. `DiscoveredArticle` содержит URL/title/raw_date; не может содержать arbitrary hosts.

- [ ] RED: rubric links `.news-block__name a`, article `div.text_block`; вложенные p не удваиваются; формы/share/scripts/скрытое archive notice не входят. RSS200/0item не healthy; `/rss_region/` не вызывается.
- [ ] RED date policy: `23.10.2026 09:30` без offset сохраняется raw+known day; точное UTC только при явно названном источнике timezone. Tax rate не cost_of_debt; срок будущего платежа не совершённый платёж.
- [ ] Run `pytest ml-service/tests/test_fns_source.py -q` → FAIL; реализовать adapter и tax tags без LLM → PASS.
- [ ] Small read-only live smoke1article: непустой содержательный исходник, raw URL/body hash/precision, атрибуция; хранить article внеGit. Никакой readiness отметки от одной HTTP200.
- [ ] Commit `feat(materials): ingest FNS news with faithful dates`; coordinator подключает registry и общий classifier после проверки diff.

Первый тест T03:

```python
def test_fns_preserves_unknown_timezone(fns_article_html):
    record = parse_fns_article(fns_article_html, "https://www.nalog.gov.ru/rn77/news/activities_fts/16664106/")
    assert record.published_date.isoformat() == "2026-10-23"
    assert record.published_at is None
    assert record.published_precision == "date"
    assert "Это архивная публикация" not in record.text  # в fixture метка hidden
```

### Task 4: Реальный источник EIA и энергетическая семантика

**Владелец:** D04 chat, отдельный EIA субагент; параллельно T03. **Gates:** G02/G03. **Файлы:** Create `materials/adapters/eia.py`, `tests/test_eia_source.py`, small `fixtures/materials/eia-*`; общие registry/classifier интегрирует coordinator.

**Интерфейсы:** `EiaTodaySource.collect(limit:int)->FetchBatch`, `parse_eia_article(html:bytes,url:str)->RawNews` и index parser по T02; `material_type=public_analysis`, geography/language/tags сохранены. Full own text only; никаких third-party images/datasets.

- [ ] RED index recent `.tie-article h1 a[href*='detail.php?id=']`; article `.tie-article`/`.date`/h1, footer/tags/more-recent удалены; meaningful id query не теряется, canonical на home/чужой host не принимается.
- [ ] RED date-only October22 сохраняется как date, не timestamp midnight; refinery utilization rates не cost_of_debt; USD/barrel, percent, forecast/negation и US geography не превращаются в рубли/российский факт.
- [ ] Assert транспорт ни разу не запрашивает EIA `/rss`/archive; разрешены только tested recent/article paths.
- [ ] Run `pytest ml-service/tests/test_eia_source.py -q` → FAIL; реализовать → PASS. Live1article/read-only, source/date acknowledgment и translation metadata проверены; полноценная статья остаётся внеGit.
- [ ] Commit `feat(materials): ingest permitted EIA energy publications`; optional MOEX не добавлять незаметно в этот task.

Первый тест T04:

```python
def test_eia_refinery_rate_is_not_interest(eia_article_html):
    record = parse_eia_article(eia_article_html, "https://www.eia.gov/todayinenergy/detail.php?id=68344")
    assert record.published_date.isoformat() == "2026-10-22"
    assert record.published_at is None
    assert record.material_type == "public_analysis"
    assert "cost_of_debt" not in record.topics
```

`parse_eia_article(html:bytes,url:str)->RawNews` и `topics:list[str]` добавляются в согласованный T02 контракт. Поле topics не заменяет уже существующий NewsEvent.topics, а переносится normalizer-ом.

### Task 5: Multi-source sync, деградация и повторяемое обновление

**Владелец:** D04 интегратор, после T03/T04. **Gate:** G04. **Файлы:** Create `materials/collector.py`, `tests/test_multisource_sync.py`; Modify `materials/cli.py`, `materials/store.py`, `materials/dedup.py`, ingestion docs.

**Интерфейсы:** `collect_sources(specs:list[SourceSpec],limit_per_source:int)->list[FetchBatch]`; `sync_sources(store:Path,batches:list[FetchBatch],observed_at:AwareDatetime)->SyncResult`; SyncResult содержит status=`complete|unchanged|partial|failed|needs_review`, exit_code/published_revision/previous_revision/report_path; source report health=`healthy|degraded|failed`. Command spec `sync --sources cbr_press,fns_news,eia_today --limit-per-source 5 --store PATH`.

- [ ] RED, три издателя: у одного timeout, у второго одна статья 404, третий успешен → exit3/report=degraded, успешные records published, старые failed-source records сохранены. Все источники недоступны или corpus validation error → exit1/previoustree unchanged. Empty→exit2 без fakehealthy;304/exact saved replay → exit0 unchanged.
- [ ] RED samebatch×2 same IDs/counts/revision; new observation same content same event/source IDs; changed number/negation creates new source version; delayed observation не rollback. Не считать изменение observation metadata новой версией содержимого.
- [ ] RED fetch finishes до lock; underlock reload fresh snapshot, параллельный bank import не теряется. Fault after partial write/pointer swap preserves previous complete snapshot. Fatal errors separate from source/item diagnostics.
- [ ] Run `pytest ml-service/tests/test_multisource_sync.py ml-service/tests/test_news_dedup.py ml-service/tests/test_material_store.py -q` → FAIL на новых поведениях.
- [ ] Реализовать collector и независимый run report/last_attempt, bounded retry/conditional GET, одну атомарную публикацию с CatalogToken. Точный replay не увеличивает generation; bank-only update продвигает generation даже при прежнем event hash. Сохранить legacy CLI0/1/2 и документировать sync3.
- [ ] GREEN+full ML; real bounded sync3publishers и повтор. Сохранить machine report sourceIDs/counts/status/hash, не full public article вGit.
- [ ] Commit `feat(materials): synchronize publishers with partial failure reports`.

Первый тест T05:

```python
def test_partial_sync_preserves_old_news(seed_store, partial_batches, observation_time):
    previous_ids = {e.event_id for e in load_snapshot(seed_store).news}
    result = sync_sources(seed_store, partial_batches, observation_time)
    assert result.exit_code == 3
    current = load_snapshot(seed_store)
    assert previous_ids <= {e.event_id for e in current.news}
    assert result.status == "partial"
```

### Task 6: Полная disposition страниц, review и расширенный bank gold

**Владелец:** D03 chat; PDF review субагенты по непересекающимся документам, один merge reviewer. **Gates:** G05/G06. **Файлы:** Modify `config/bank-documents.json`, `eval/bank-review.json`, `eval/bank-retrieval-gold.jsonl`, `eval/run_bank_retrieval.py`; Create `eval/bank-page-review.json`, `eval/bank-retrieval-extended.jsonl`, `tests/test_bank_review_coverage.py`; private OCR corrections внеGit.

**Интерфейсы:** page decision identifies doc_id/page/extraction_revision/text hash/disposition/reason; verified fragment decision exacthash/type/period/valid_until/supersedes. Gold задаётся до настройки поиска и содержит query/as_of/expectedIDs/page_checked/reason. Не добавлять raw quote/PDF вGit.

- [ ] Зафиксировать distribution63 ready + 1 needs_review,11 verified / 1434 pending; каждую из 64 страниц визуально классифицировать, включая cover/chart/legal/stale/unknown-date. Существующие OCRcache/raw не трогать.
- [ ] RED test requires64dispositions, verified has exact image/page/hash, no unknown/stale/future/expired assessment; changed text resets review; stale steel и undated Gold не получают guessed date.
- [ ] Run coverage test → FAIL на прежнем неполном ручном покрытии. Добавить реальные решения, целевойне менее 30 verified из 6 документов с известной датой и по 6 темам. Если материала нет — gap/явное решение об ограничении объёма, не формальную отметку verified без проверки.
- [ ] Тимур или независимый разметчик фиксирует12 новых положительных запросов и 6 близких отрицательных примеров до retrieval tuning; старый10 не меняется. Near-negatives включают tax vs key rate, refinery rate, unrelated corporate manipulations, future/expired/undated.
- [ ] Запустить coverage и gold: старые 10 запросов, не менее 10/12 в top-3 на расширении, 6/6 корректных отказов. При измеренном gap минимально исправить normalize/query/topic policy и написать failing retrieval test; BM25 сохраняется, новые embeddings без необходимости не вводятся.
- [ ] Commit `feat(materials): complete auditable bank review and retrieval acceptance`; private images/page texts остаются локально с manifest.

Первые assertions T06:

```python
def test_bank_release_coverage(reviewed_snapshot, page_decisions):
    assert len({(d.doc_id, d.page) for d in page_decisions}) == 64
    approved = [f for f in reviewed_snapshot.bank_fragments if f.review_status == "verified"]
    assert len(approved) >= 30
    assert len({f.doc_id for f in approved if f.doc_date is not None}) >= 6
```

### Task 7: Закреплённый runtime-корпус, каталог и исторические источники

**Владелец:** координатор ML runtime. **Gates:** G04, G07. **Файлы:** Create `materials/runtime.py`, `tests/test_corpus_runtime.py`, `tests/test_material_catalog.py`; Modify `materials/api.py`, `materials/policy.py`, `api.py`, `config.py`, `agent/state.py`, `agent/store.py`, `agent/orchestrator.py`, `agent/qa.py`, `agent/versions.py`, `tools/news.py`.

**Интерфейсы:** `CorpusRegistry.current()->CorpusBundle`, `get(revision:str)->CorpusBundle`. Bundle неизменяемый: snapshot, bank, news. В сохраняемом state находятся revision/hash, а не runtime lock/index. `CatalogResponse` содержит CatalogToken, events и next_cursor. `MaterialEvent` содержит NewsEvent и event_content_sha256. InsightRequest получает optional corpus_revision; Insight/Answer/state/trace сохраняют её. `as_of` в MVP — publication cutoff с явно описанным ограничением, не bitemporal replay.

- [ ] RED: run A начинает на R1, pointer меняется на R2 во время вызова инструмента — все основания A остаются R1, run B получает R2. Повреждённый новый bundle не становится активным; health показывает ошибку обновления. Нельзя менять общие agent.bank/news посреди запроса.
- [ ] RED: QA старого инсайта после рестарта ищет в R1; если R1 отсутствует, возвращает no_answer с причиной. Старый PDF открывается по revision после удаления документа из активного корпуса. Wrong ID/path/revision отвергается.
- [ ] RED: ответ каталога и cursor закрепляют полный CatalogToken. Обновление банковского корпуса R1→R2 при прежнем событии продвигает token; поздний sync R1 отвергается. Historical lookup не может продвинуть current head. Неверный corpus/event hash обнаруживается до платного вызова.
- [ ] RED: correction наблюдалась 26.10 при прежней publication date 20.10, запрос cutoff 21.10. В режиме publication cutoff допускается нынешняя версия, но результат обязательно limited с предупреждением о невозможности point-in-time реконструкции; старый принятый run воспроизводится только на его сохранённой revision. Unknown/day-only не становятся точным intraday timestamp. Проверить known day-only event23.10: bank cutoff23.10, а не current26.10; более поздний bank doc исключён. `_as_news_event` для research не изобретает midnight и не путает effective date индикатора с publication date.
- [ ] Запустить новые runtime/catalog tests: ожидается FAIL. Реализовать registry, token, исторический lookup и обёртки существующих tools, не создавая новый agent loop. Cache ограничен; evicted bundle перечитывается из immutable store, а не заменяется текущим.
- [ ] Получить PASS новых и прежних ML-тестов. Fingerprint анализа учитывает semantic content, effective as_of, prompts/rules; новый fetch неизменного текста не считается новым событием. Commit `feat(materials): pin catalog and evidence to corpus revisions`.

### Task 8: Сохранить контракты ML → Java → UI

**Владелец:** Java/ML integration chat с Левоном, после T01/T02 и DevOps baseline. **Gate:** G07.

**Путь J:** `backend-api/src/main/java/ru/businessinsights/backend`. **Файлы:** Modify `J/ml/MlContracts.java`, `J/ml/HttpMlClient.java`, `J/api/ApiModels.java`, `J/service/InsightMapper.java`, `J/data/CompanyProfileService.java`, `backend-api/pom.xml`, `backend-api/src/main/resources/static/openapi.yaml`, `backend-api/src/main/resources/ml-contracts/*.schema.json`; Create `backend-api/src/test/java/ru/businessinsights/backend/ml/MlEvidenceContractTest.java`.

**Интерфейсы:** SourceRef сохраняет opaque ID, doc/hash/excerpt/publisher/kind/date precision, first_seen_at/retrieved_at и тип/горизонт банковского утверждения. Сохраняются claim_id, массивы claims, conflicts, outcome/error_code/request_id/as_of/corpus_revision/event hash. Профиль содержит year/status/scope/FTS availability/payment period/quality notes. Generated ML schemas — источник истины; публичный UUID Java не равен logical event/source ID.

- [ ] RED round-trip: две claims, два конфликтующих источника со всеми metadata, observed/known, 0/999, Decimal amount, refused/none/limited. После JSONB serialize/read/API mapping семантические поля сохранены, а не просто успешно проигнорированы.
- [ ] RED: неверная schema, отсутствующий source и false profile flag отвергаются. Уже исправленные ML-ограничения не вызывают устаревшие compatibilityWarnings и forced limited.
- [ ] Запустить `mvn -f backend-api/pom.xml test`: ожидается FAIL новых проверок. Добавить JUnit/Spring test harness к существующему проекту и реализовать DTO/mapping/schema refresh. Второй API не создавать.
- [ ] Получить PASS Java-тестов, ML `test_backend_contract` и schema export diff. Закрепить source SHA схем и обновить устаревшую документацию.
- [ ] Commit `fix(backend): preserve material evidence and current ML outcomes`.

### Task 9: Версии каталога Java и замороженные входы очереди

**Владелец:** тот же Java lane, последовательно с T08; завершение после T07. **Gates:** G04, G07. **Файлы:** Create `J/ml/MaterialCatalogClient.java`, `J/data/MaterialCatalogRepository.java`, `backend-api/src/main/resources/db/migration/V2__material_catalog_context.sql`, tests `service/MaterialCatalogLifecycleTest.java`, `service/QueuedInputSnapshotTest.java` под тестовым package из T08; Modify `J/store/OperationStore.java`, `J/service/InsightService.java`, `J/api/BackendController.java`, request DTO/OpenAPI.

**Интерфейсы:** trusted sync получает полный CatalogToken и MaterialEvent. CAS сравнивает ожидаемый head token; одинаковый token идемпотентен, меньшая generation отвергается, другой store требует явного reseed. Legacy `/api/v1/events` остаётся immutable/409. До очереди сохраняется snapshot profile+event+role+as_of+corpus_revision+request IDs. Execute читает его. Каталог доступных событий отделён от ленты уже подготовленных инсайтов.

- [ ] RED: тот же logical event ID с новым source hash обновляется через trusted catalog; legacy manual registration по-прежнему 409. Точный повтор не создаёт дублей. Обновление только банка продвигает corpus token для следующего анализа без изменения event ID. Запоздалый R1 после R2 не откатывает head.
- [ ] RED: submit на R1/P1, затем refresh R2/P2 до worker — фактический ML input остаётся R1/P1. Старый request key возвращает старую операцию; тот же key с другим immutable input даёт 409. Новый key создаёт отдельный superseding result.
- [ ] RED на настоящей временной PostgreSQL: старая операция/ответ/trace/source остаются R1 после рестарта; проверены user/company/role isolation и путь «новое событие → анализ → prepared feed». Corpus mismatch до генерации даёт явный error code и 0 provider calls.
- [ ] Запустить `mvn -f backend-api/pom.xml test` с disposable PostgreSQL17+ и отдельным `BI_JDBC_TEST_URL` тестового harness: ожидается FAIL новых проверок. При отсутствии test DB результат NOT_RUN, не PASS. Реализовать минимальный current catalog CAS и frozen payload, не меняя data-import schema. Не заменять это простым upsert; он вводит queue TOCTOU. Массовая генерация по всем клиентам не запускается сбором новостей.
- [ ] Получить PASS unit и фактической DB integration, проверить сохранённые старый/новый контексты. Отдельная произвольная event history table не требуется при immutable ML snapshots и сохранённых operation inputs.
- [ ] Commit `feat(backend): version material catalog and freeze analysis inputs`.

### Task 10: Evidence spans и смысловые регрессии вместе с ML

**Владелец:** Ангелина/ML lane; D03 предоставляет проверенные spans, D04 — negative fixtures, Тимур принимает. **Gate:** G09. **Файлы:** Modify `schemas.py`, `agent/analyst.py`, `personalizer.py`, `critic.py`, `prompts.py`, `qa.py`, `orchestrator.py`, `versions.py`, `evaluation.py`; Create `tests/test_semantic_evidence.py`, `eval/material-semantic-cases.jsonl`, `eval/material-semantic-rubric.md`.

**Интерфейсы:** `EvidenceSpan(source_id,content_sha256,start,end)` — optional legacy-compatible поле Claim. `validate_span(span:EvidenceSpan,text:str,expected_sha256:Sha256)->str` возвращает полное исходное предложение/проверенный bank fragment; при неверных hash/bounds/boundaries бросает ValueError. Offsets — Unicode code points [start,end), bank span=весь fragment, не page offsets. `SemanticVerdict(claim_id,status,reason,span_ids)` использует существующий StructuredLLM и общий budget/deadline. Второго автономного клиента нет.

- [ ] RED на конечном опубликованном результате и QA: эмитент→манипулятор, flipped negation, будущий платёж→завершённый, old-period FX→current, percent/bp/currency/sign, old13.5→revised14, недостающие детали аннотации, US→RF, перевод EIA, приписывание внешнего источника банку, потеря условия, выдуманная экспозиция компании.
- [ ] RED: wrong offset/hash, обрезанное отрицание или неизвестный span не поддерживают fact/assessment. Точная банковская цитата сохраняет дату/горизонт. Нельзя окружить правильную цитату ложным внешним утверждением в inference.
- [ ] Запустить semantic tests: ожидается FAIL на воспроизведённых дефектах. Реализовать faithful rendering фактов/банка, structured verdict для generated external assertions и fail-closed путь. Версия prompts/rules меняется вместе с invalidation cache.
- [ ] Получить PASS новых и прежних тестов; проверить лимит вызовов verifier, токенов и времени. При schema/budget/verifier failure unchecked draft не публикуется. Deterministic rule, не умеющее судить arbitrary entailment, не объявляется доказательством истины.
- [ ] Commit `fix(ml): validate source spans and reject semantic substitutions`. Совместить full/limited/refused с Java/UI; offline PASS ещё не означает принятия A10.

### Task 11: Реальный UI-сценарий, PDF и QA

**Владелец:** integration chat с UI/DevOps owners. Писать после их committed baseline, не параллельно с proxy/bindings. **Gate:** G08. **Файлы:** Modify `frontend/web/backend-bindings.js`, `frontend/web/integration.js`, `frontend/tests/test_backend_bindings.cjs`, текущие Python/UI tests; при необходимости согласованно `infra/nginx/config.js`, `infra/nginx/default.conf.template` и Java public/source routes; Create `docs/materials-e2e.md`.

**Интерфейсы:** browser → существующий same-origin gateway → Java `/api/v1/*`. Bearer не хранится в JS. Raw material catalog и prepared insights различаются. Source URL абсолютный, доступен браузеру и содержит revision/page; opaque ID не разбирается по двоеточию. Outcome/errors/status mapping согласован с PR7 binding.

- [ ] RED на реальных Java DTO: source metadata, все claims, refused/no-data/errors; 202 Location/Retry-After polling, отсутствие повторного POST, reconnect к той же операции, wrong tenant 403. Release server mode не подменяет ошибку demo API.
- [ ] Запустить три Node suites и Python UI tests: ожидается FAIL недостающего mapping. Расширить готовый binding PR7, не писать новый транспорт. Demo остаётся отдельным явно выбранным режимом.
- [ ] Пройти реальный disposable-stack browser smoke: новый event каждого publisher, анализ, bank/AI separation, click original article и правильная физическая PDF page. Excerpt/hash/claim ID соответствуют trace. QA, opened/hidden/feedback сохраняются после рестарта.
- [ ] Сохранить browser evidence/run IDs. URL unit assertion не заменяет навигацию. Внешний заблокированный шаг остаётся NOT_RUN с владельцем.
- [ ] Commit `feat(integration): connect material evidence to the real user flow`.

### Task 12: Совместная release-приёмка и передача

**Владелец:** coordinator и Тимур; владельцы компонентов исправляют свой слой. **Gates:** G01–G10, A01–A13. **Файлы:** Update `docs/materials-acceptance.md`, eval manifests/results/README, `THIRD_PARTY_NOTICES`, README и source/runtime docs; добавить offline CI существующих тестов, если его нет. В Git только metadata, не private inputs/outputs.

**Интерфейс:** release manifest фиксирует git/prompt/schema/corpus/profile/policy versions, CatalogToken, original URLs/hashes, publisher count, 64 page dispositions и verified count, gold/negative IDs, 9 run и 9 QA IDs, ручные verdicts, budget/latency и результат независимого запуска.

- [ ] Выполнить ML, Java, PostgreSQL и UI tests на одном integrated SHA. Все обязательные проверки проходят, skips не переименованы в PASS. Schemas совпадают с export, v1 корпус открывается, секреты/raw/full articles не отслеживаются. Offline CI использует fixtures; live job запускается явно.
- [ ] Real bounded sync трёх publishers, replay, correction, один недоступный source; review/gold/near-negatives; refresh при in-flight run и старом QA; браузерные оригиналы. Ожидается фактическое доказательство G02–G08.
- [ ] До live зафиксировать rubric/holdout, разрешённую модель, actual pricing и остаток общего ключа. Session cap не более100руб и доступного остатка из500руб; reset03:00МСК. Использовать существующие budget/deadline guards. Обычная сборка источников и CI не делают provider calls.
- [ ] Выполнить9новых event×profile пар и≥9QA. Во всех опубликованных полях0фактологических/атрибуционных ошибок,0failed при исправных входах, полезные различия профилей. Проверить bank influence, no-bank, update, missing-profile и degradation. Дефект становится regression; для его класса берётся новый holdout.
- [ ] Второй участник по root Compose инструкции делает импорт, новое событие, browser source/QA/trace и refresh. Зафиксировать hardware/browser/env, versions и ограничения. Синтетический smoke PR7 не повторно назвать real live приёмкой.
- [ ] Fresh branch review субагентом; Critical/Important воспроизвести тестами и исправить; финальный зелёный набор. Commit docs, push feature PR(s), attach artifacts. Ready только при mandatory gates PASS; иначе draft со списком BLOCKED/NOT_RUN и владельцами.

## Объём и оценки

Обязательный finish scope — T01–T12. MOEX/EEC/другие publishers, embeddings, semantic clustering, archive backfill и произвольный historical event browser — отдельные расширения. Они не маскируются как сделанные и не задерживают согласованный базовый результат.

Для регулярного потока DevOps запускает тот же sync по интервалу900сек с process lock, backoff и report после готовности T07/T09. Ручная команда остаётся диагностикой. Scheduler не исправляет source coverage и смысловое качество. Точный исторический point-in-time отбор версий не входит в MVP: publication cutoff и его ограничение явно показаны пользователю.

Оценка для декомпозиции, не обещание срока: source/compat/sync12–18ч; review/gold6–10ч; runtime/Java/UI contracts12–20ч; shared semantic/acceptance8–16ч. Итого38–64ч командной работы без уже выполненного отдельного DevOps и ожидания участников. При параллельных lanes — ориентировочно3–5рабочих дней; одному разработчику5–8дней. После T01/T02 исполнители уточняют оценку. Из старых предложений20–22октября не выводится новый дедлайн26.10.

**Handoff:** пользователю передаются spec, этот план, research reports и карта чатов. Выбранный метод — чаты и субагенты; повторно выбирать его не требуется. Новые D03/D04 implementation lanes стартуют после согласования плана. DevOps PR7 уже создан и рассматривается отдельно; shared branches этим планом автоматически не сливаются.
