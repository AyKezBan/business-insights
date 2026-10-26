# Финализация D03/D04: несколько источников и доказуемый сквозной результат

**Дата:** 26.10.2026, Москва. **Статус:** предложение для согласования; новая реализация D03/D04 не начата. **Владелец:** Мурад; совместные границы — Ангелина (ML), Левон (Java/data), Тимур (приёмка/UX), Лёша (UI). Общий DevOps выполняется отдельным уже созданным чатом.

**Основание:** [контекст](../../research/2026-10-26/00-context-and-scope.md), [ветки](../../research/2026-10-26/01-branches-integration.md), [открытые источники](../../research/2026-10-26/02-open-sources.md), [приёмка](../../research/2026-10-26/03-acceptance-gaps.md). Результаты относятся к закреплённым SHA и указанным HTTP-пробам, не к обещаниям uptime.

## 1. Какой результат нужен

Новая публикация каждого поддерживаемого издателя без правки кода проходит сбор, проверку, версионирование, публикацию корпуса, каталог Backend и пользовательский анализ. Пользователь видит отдельно первичный факт, датированную экспертизу банка и AI-вывод, открывает оригинальную статью/PDF на нужной странице, задаёт вопрос и получает trace той же версии. Повтор, исправление, частичный сбой и рестарт не теряют историю и не подменяют доказательства.

Число парсеров само по себе не является готовностью. Финализация включает расширение источников, пригодное покрытие банковских материалов, сохранение provenance в Java/UI, исправления lifecycle/versioning и совместную смысловую приёмку. Объём ниже выбран как инженерная рекомендация, а не приписанное кейсу правило.

## 2. Текущее состояние и база

- `develop=e105b7cda044b9ea69b0e37e3394decc1f59b014`: PR2(data),3(ML),4(D03/D04),5(Java) уже merged.
- ML `416f24fcce459090580fdb9cb4a036c335621b14`:242 offline tests; Decimal, budget/deadline, as_of, idempotency, persisted insights, research/cache/indicators, evaluator и новые guards. Переиспользовать полный согласованный merge.
- UI `7c59dd0a7073ee0eb5bdee4f53599eacfe2bf221`:20 Python tests и2 Node suites; server transport подготовлен, реальные bindings требуются.
- develop ML115 tests; старый gold10/10 и3/3 no-coverage воспроизведены. Это не A10.
- Corpus:9PDF/64страницы,63ready+1needs_review,1445кандидатов,11verified на4страницах/4документах. Эти11 проверены; оставшиеся1434 не объявляются пригодными.
- Persisted ingestion знает один publisher CBR. Новые CBR pages/indicators в research не считаются новыми издателями и не попадают сами в MaterialStore.
- Java теряет doc/hash/excerpt, conflicts, часть профиля и новые outcome/request/time fields; changed event body с тем же event_id получает409. Сохранённые операции не закрепляют corpus revision. QA после восстановления может искать в уже другом активном корпусе.
- Старый live6=5limited/1failed; свежие synthetic probes всё ещё допускают подмену участника, отрицания и будущего действия. A10 не принят.

Перед исполнением повторить fetch/проверку SHA. Старые detached checkout и a3e5e14 worktree не использовать как интеграционную базу. Merge-tree доказал один конфликт ML `.gitignore`, один UI `frontend/README.md`; полное дерево ML не копировать поверх develop. Если ссылки продвинулись, повторить симуляцию и тесты.

## 3. Портфель источников

**Рекомендуемая обязательная тройка — три издателя, полный собственный текст:**

| Registry ID / publisher | Получение | Роль и границы |
| --- | --- | --- |
| `cbr_press` / `cbr` | Существующий RSS+`.landing-text` | РФ, финансы/ставка/регулирование; CBR indicators отдельно |
| `fns_news` / `fns` | Разрешённая HTML-рубрика ФНС `rn77/news/activities_fts/`, затем статья `div.text_block` | Налоги/сроки/обязанности бизнеса. Регион/применимость и вступление нормы в силу отдельно от news date |
| `eia_today` / `eia` | Разрешённый recent HTML Today in Energy, затем `.tie-article` | Собственные энергетические публикации EIA; США/международный контекст, не факт о российской компании и не позиция Альфа-Банка |

Проверенные URLs/селекторы/rights/robots приведены в отчёте02. ФНС RSS даёт200/0item — использовать работающий HTML, не считать пустой ответ успехом. EIA `/rss` запрещён robots — не вызывать; разрешён `/todayinenergy/`. Условия EIA требуют атрибуции источника/даты и пометки перевода; сторонние фото/графики/datasets не копируются. ФНС требует интерактивную ссылку и отделение исходного текста от AI-обработки.

Опционально P1: `moex_annotations`, только разрешённые title/description, `material_type=publisher_annotation`, completeness partial и10сек host delay. Не выдавать `content:encoded` или полный HTML за разрешённую аннотацию. ЕЭК — кандидат после документированного решения по reuse; Росстат/Правительство/ФАС — после успешной проверки из среды запуска. РБК/Интерфакс не обещать как свободные full-text adapters; обхода условий, paywall, TLS или robots нет.

Если команда выбирает только российскую географию, меняется portfolio spec: EIA нельзя молча заменить неподтверждённым российским сайтом. Это явная точка согласования портфеля, не новый обязательный источник из кейса.

## 4. Архитектура и контракты

Существующий Python ML/materials остаётся единственным конвейером; Java — публичным API/хранением операций; frontend работает через согласованный Java binding. PostgreSQL/data-import не переписываются; новый брокер, vector DB, Redis и отдельный ingestion-сервис не нужны. Существующие BM25, snapshots, merge_news, review hashes, agent loop и ML research переиспользуются.

### 4.1. Source metadata и версия схемы

`RawNews`/`SourceVersion` получают publisher_id, adapter_id, discovered_url, language, material_type, completeness, parser_version, rights_url и явно проверенные publication metadata и topics. Исходное тело материала сохраняется отдельно от нормализованного представления; HTML не исполняется и scripts/forms не выводятся пользователю как статья. SourceRef получает optional publisher_id/adapter_id/material_type/completeness/language/publication precision, first_seen_at/retrieved_at, statement_type/target_period/valid_until и rights/translation attribution. Они проходят Java/UI; отсутствие новых полей в legacy допустимо. Оригинальный final URL сохраняется; canonical принимается только на разрешённом host/path и не стирает CBR file/EIA id. Классификация не считает regional mirrors ФНС независимыми подтверждениями.

Publication: `published_at` — только timezone-aware timestamp из источника либо явно настроенная/помеченная source timezone; `published_date` — известный календарный день; `published_precision=timestamp|date|unknown`; raw значение и происхождение timezone сохраняются. NewsEvent тоже получает optional published_date/published_precision из primary SourceVersion; date-only не теряется при merge/каталоге. Bank policy берёт известный день события, а не сегодняшний fallback, и ограничивает его as_of; неизвестность помечается явно. Для ФНС timezone по образцу неизвестна: по умолчанию day-only до документированного source policy. EIA day-only. Не изобретать UTC midnight и не заменять публикацию временем fetch.

`material_type=news_article|public_analysis|publisher_annotation|indicator`. Indicators хранят effective date, Decimal value/unit отдельно и не засоряют news feed как новые статьи. `public_analysis` EIA не становится bank kind. Forecast/condition/effective date не превращаются в произошедший факт.

Новый snapshot schema v2 загружается наряду с v1. Legacy hash проверяется на исходном canonical JSON без новых defaults до in-memory migration; старые файлы/revision не перезаписываются. Новый publish создаёт v2. Golden compatibility fixture плюс локальный корпус169bef4… обязаны загружаться. При несовместимости loader отказывает явно, без fallback на demo.

Fresh observation может менять corpus revision по прежней timestamp-политике, но не event/source-version ID. Это допустимый именованный контракт: не обещаем стабильную live revision при новом наблюдении. Точный replay сохранённого batch сохраняет IDs/counts/revision. Source health timestamps и parser failures живут в отдельном run report, не в `snapshot.report.errors` и не в identity события.

### 4.2. Source registry и transport

Fixed registry содержит publisher/adapter IDs, HTTPS hosts/paths, discovery/extraction/date/rights policy, timeout, max bytes, interval и concurrency. Registry выбирает адаптер по ID; произвольный URL от пользователя/LLM не открывается. Общий SafeFetcher переиспользуется/выносится из teammate research, сохраняя CBR indicator API и старый NewsSource контракт через wrapper.

Ограничения по умолчанию:2MiB decoded response,10сек request timeout, максимум4redirects с проверкой каждого перехода,1concurrent request/host, минимум2сек между запросами; более строгие policy обязательны (MOEX10сек). Retry только transient/429, максимум1повтор с capped Retry-After≤30сек;401/403/challenge/robots denial — blocked без обхода. Conditional GET при поддержке,304=unchanged. В unit tests fake clock, без медленных sleeps.

Topics расширяются проверенными RU/EN фразами/тегами: tax/reporting/deadlines, energy/input_prices/production_capacity и существующие каналы. Голое `rate` не делает refinery utilization rate процентной ставкой; `налоговая ставка` не равна `ключевой ставке`. Geography и applicability ограничивают вывод, а не создают новую якобы известную экспозицию компании. Налоговый режим/обязанность компании не выводится из одной новости ФНС; при неизвестности нужен условный вывод и missing_data. FTS не доказывает направление торговли, а американский refinery rate не становится стоимостью кредита или фактом о российском предприятии.

### 4.3. Multi-source sync и публикация

Новая команда `insight-materials sync --sources cbr_press,fns_news,eia_today --limit-per-source 5 --store PATH`; старые `news --source cbr`, saved, bank, link, check остаются совместимыми.

Fetch не держит writer lock. Под lock загрузить последний snapshot повторно, merge всех принятых records и одну atomic publication. Сохранять старые записи failed publisher; не удалять их из-за пустого fetch. Per-item404/selector error не отменяет другие item. Пустой discovery=empty с диагностикой, не healthy. HTML challenge/menu-only/пустое тело не публикуется как статья.

Коды sync:0all_ok/unchanged;1всё failed/целостность не пройдена, pointer прежний;2review/нет пригодных данных без publish;3часть источников/item failed, полезный snapshot published или прежние пригодные данные доступны, report=degraded. Legacy bank/news semantics0/1/2 сохраняются. SyncResult.status=`complete|unchanged|partial|failed|needs_review`; report health отдельно `healthy|degraded|failed`, так что partial соответствует degraded без переименования DTO. Чётко печатать published_revision и previous_revision, publisher counts, last-success, failures/rejected; nonzero3 не интерпретировать как «ничего не опубликовано».

Run report неизменяемый с run_id; `last_attempt.json` атомарно указывает на последний report. Ошибка сборки report не должна выдавать неподтверждённый успех. Отдельные fatal corpus validation errors по-прежнему блокируют publish. All-fail не очищает ленту.

### 4.4. Runtime corpus и запросы

`CorpusRegistry` загружает/проверяет immutable bundle конкретной revision; current pointer перечитывается при входе нового запроса. Новый bundle строится полностью до замены активной ссылки. Catalog-only refresh без новой генерации не запускает LLM fan-out. In-flight request закрепляет revision; глобальные `agent.bank/news` не меняются посреди run. Ограниченный cache исторических bundles; при отсутствии/порче нужной revision — явный отказ, не подмена current.

Каталог имеет отдельно от event hash токен публикации `CatalogToken(store_id, generation, revision)`. Store ID закреплён writer-ом при создании/первом upgrade каталога, не генерируется заново readonly serving процессом; generation увеличивается под writer lock при смене опубликованной revision, включая bank-only/metadata update. Точный replay не увеличивает generation. Pointer и token публикуются атомарно; v1 pointer без token доступен для historical lookup с generation0; до trusted current-catalog sync writer выполняет явную v2 инициализацию и закрепляет store ID. Старый raw/snapshot не меняется. Backend продвигает catalog head CAS по ожидаемому полному token, принимает только более новую generation того же store, точный повтор идемпотентен, запоздалый token отвергается. Смена store требует явного reseed. При неизменном event hash и новом банковском корпусе catalog head продвигается: новые анализы получают новую revision, старые операции не меняются. Historical lookup не может продвигать current head.

Материал-каталог ML: `GET /v1/materials/events?revision=...&cursor=...&limit=...` и `GET /v1/materials/events/{event_id}?revision=...`. Полный trusted server-to-server DTO содержит NewsEvent+source version refs/hash+precision+corpus revision+event_content_sha256. Ответ содержит CatalogToken; cursor привязан к нему и revision, иначе409. Health отдельно показывает active/serving revision, last attempt/run, publishers/statuses и counts, reviewed coverage.

InsightRequest получает optional corpus_revision для legacy совместимости; new catalog requests передают её. Run/Insight/Answer/state/trace сохраняют corpus_revision и semantic event hash. QA использует свой сохранённый контекст и pinned revision, а не нынешний общий индекс; если исходный bundle недоступен — no_answer/explicit missing_data. MVP `as_of` имеет явно ограниченный смысл **publication cutoff**: источники с более поздней датой публикации исключаются, при unknown/day-only точный intraday допуск без основания не даётся. Это не реконструкция версии, известной рынку в прошлом: исправление26.10 с сохранённой датой публикации20.10 может присутствовать в нынешнем корпусе при cutoff21.10. Такой historical request помечается limited с предупреждением, observed/retrieved/version явно показаны; `complete` и подпись «point-in-time replay» для него недопустимы. Истинный bitemporal selection по наблюдаемым версиям — вне MVP. Точное воспроизведение старого принятого run опирается на его сохранённую corpus revision, не на guessed исторический индекс.

PDF URL — абсолютный browser-reachable public base, document ID, `?revision=<sha>#page=N`. Реальный public route/proxy задаёт DevOps/Java; internal `http://ml:8000` и относительный URL, который Java отвергает, в browser DTO недопустимы. Old run продолжает открывать immutable оригинал после refresh. Нельзя добавлять path из fragment ID или произвольный URL в proxy.

### 4.5. Java event lifecycle и preservation

Новые field source doc/hash/excerpt/publisher/date precision/material kind, claim_id, conflicts, outcome/error/request/as_of и profile uncertainty сохраняются Java DTO → JSONB → API → UI. Backend-копии JSON Schema генерируются/закрепляются на выбранном commit, warnings о старых уже исправленных ограничениях удаляются. Нельзя «исправить» проблему одним ignoreUnknown.

Legacy `POST /api/v1/events` сохраняет immutable semantics/409. Для trusted material catalog — отдельный CAS update по полному CatalogToken и event_content_sha256; стабильный logical event ID не меняется от исправления статьи. При submission analysis **до очереди** атомарно сохранить полный event/profile snapshot, corpus revision, event hash, role, as_of и request id. Execute использует frozen operation input, не перечитывает текущий event/profile. Иначе простой upsert вводит race: старый queued запрос получает новые факты.

История материалов остаётся в immutable ML snapshots и источниках; старые операции хранят собственные inputs/results. Для MVP не обязательна отдельная таблица произвольной event history, если CAS+frozen op payload дают эти гарантии. Migration добавляет только нужный current-catalog context/index, не меняет data-import schema. Повтор операции с тем же ключом не платит повторно; ключ с другим immutable контекстом даёт409.

Backend feed сейчас содержит готовые insights, а не сырой news catalog: frontend должен различать «доступные события» и «мои инсайты». Сбор публичных источников сам по себе не запускает платную генерацию для всех клиентов.

### 4.6. Research и ingest

Не строить второй researcher. CBR key-rate/FX и TTL-cache из ML-finalize сохраняются. Registry/transport/provenance едины, но indicator observation не становится новым news article. Динамические документы сохраняются в evidence state конкретного run с original URL/hash/effective/retrieved/precision; их нельзя считать вошедшими в corpus revision, пока они явно не опубликованы. Такой import при необходимости проходит тот же typed normalizer, а не прямую запись в index. Cached observation сохраняет фактический fetched time.

## 5. Банковское evidence и смысловая безопасность

Каждая из64страниц получает явное review disposition: содержит usable narrative / таблица-график без надёжного extraction / служебная / устаревшая / дата неизвестна / rejected. Это не автоматическое verified1434фрагментов. Рабочая цель: минимум30проверенных самостоятельных утверждений из≥6документов с известной датой, ≥6сценарных тем. Если фактическое содержание не даёт этот объём, фиксируется gap и корректируется сценарий; дата или позиция не выдумывается.

100% bank claims финальных сценариев сверены с изображением: числа, units, отрицания, условность, period, valid_until, supersedes. Steel2021 не становится текущим, Gold с неизвестным днём не получает guessed date; истёкший forecast исключён. Legal/cover страницы разрешено явно исключить. Исправления OCR сохраняют старую страницу; изменённый hash требует нового review.

Старый gold10 — regression. Тимур до настройки определяет extension12положительных запросов по≥6темам и6near-negatives; цель≥10/12top3 и0ложной позиции на6near-negatives. Отсутствующие темы должны быть названы, не нарисованы в gold. Embeddings/reranker добавлять только после измеренного recall gap.

Чисто числовая валидация не доказывает смысл. Предлагаемая стратегия совместно с Ангелиной: fact и bank assessment получают EvidenceSpan(source_id, content_sha256, start, end) и отображаются из проверенного полного исходного предложения/банковского fragment. Start/end — Unicode code-point offsets полуоткрытого интервала [start,end) в разрешённом тексте именно этого source_id, hash — UTF-8 SHA256 этого текста. Для bank source span охватывает весь проверенный fragment (0..len(fragment.text)); page char_start/char_end — отдельные offsets и не подставляются сюда. Generated interpretation остаётся inference/hypothesis с отдельными основаниями. Единицы/валюты/география/роль участника/условие/время не меняются ради сокращения или перевода.

Для выводов с внешними фактическими утверждениями существующий verifier дополняется structured semantic verdict в том же StructuredLLM/budget/deadline, без второго автономного клиента. При неподтверждённости claim удаляется/переформулируется как действительно условный вывод либо отказ; не публиковать его как verified. Ошибка verifier даёт fail-closed ограничение. Exact source span не разрешает произвольное ложное объяснение вокруг него. Это снижает риск; доказательство достаточности — независимая приёмка, не обещание идеального entailment.

## 6. Приёмка и бюджет

Ниже проектные пороги для согласования; исходные A01–A13 сохранены в отчёте03.

| Gate | Доказательство закрытия |
| --- | --- |
| G01 integration | Свежая integration base сохраняет Java/import/материалы; ML242+newtests и UI20+Node проходят; реальные schemas согласованы |
| G02 publishers | По≥1реальному непустому материалу каждого из3издателей; publisher identity и original URL/hash/type/precision сохраняются до UI/trace; EIA attribution/translation явны |
| G03 normalization | Минимум12small fixture кейсов: CBR query, FNS timezone/hidden/duplicate p, EIA day-only/ID/rate/units, annotation partial, redirect/rights denial;0потерянных условий/чисел в нормализованном исходнике |
| G04 lifecycle | Replay, live unchanged, same-URL correction, negation change, delayed historical observation, partial/all-fail, crash/pointer, parallel writer, refresh during run, old QA после refresh — все с сохранённой историей |
| G05 bank coverage |64page dispositions;≥30verified/≥6dated docs/≥6тем либо явно принятый scope gap;100% используемых assessment reviewed,0pending/expired/future/undated |
| G06 retrieval | Old10 regression; extension≥10/12top3;6/6near-negative refusals; даты/supersession отдельно |
| G07 Java preservation | Typed/schema/JSONB/API round-trip новых fields; changed event поддерживается CAS по publication token продвигает bank-only update, запоздалая публикация отвергается, frozen queued op не дрейфует, wrong-role/tenant rejected |
| G08 browser | Новый event из каждого publisher→реальная UI-карточка, два разных профиля, правильная PDF page/новостной original URL, QA/trace, feedback; auth/async/errors не mock |
| G09 semantic/A10 |9новыхevent×profile пар и≥9QA;0ошибок участника/отрицания/даты/числа/unit/модальности/банковской атрибуции во всех опубликованных полях;0failed на корректных входах;limited только полезный/объяснённый |
| G10 reproduction | Второй участник по одной инструкции запускает общий Compose, импорт3publishers/банк, refresh, новый scenario, source link/QA; фиксирует версии/результат |

9пар:3новых события, по одному от каждого publisher; каждый проходит через2профиля одной отрасли с разными продуктами/экспозицией и третий другой/неполный профиль. До live есть12adversarial synthetic cases: участник/объект, отрицание, будущее/факт, число/unit/sign/период, bank-vs-public, перевод/география, missing company exposure. Holdout не используется для настройки; дефект становится regression, соответствующий класс оценивается на новом holdout. Хотя бы1принятый сценарий показывает заметное влияние банка на вывод;1no-bank,1update,1missing-profile и1degradation обязательны.

Offline/synthetic transport не выдаётся за real LLM/browser/DB. Live только после offline gates, через6разрешённых моделей и выданный gateway, с actual pricing/остатком ключа, общей ledger и120сек/run deadline из ML (изменение обоснованно документировать). Предлагаемый предел финальной evaluation session —100руб и не более доступного остатка из500руб/день, reset03:00МСК; это выбранный cap, не подтверждённый текущий баланс. На timeout/schema/budget ошибках bounded stop, не бесконечный retry и не смена модели на запрещённую.

## 7. Чаты, владение и вне объёма

Рабочие lanes: этот чат — интегратор/spec/contracts; чат источников D04; чат банка D03; чат совместной приемки с Angélina/Java/UI; отдельный уже активный DevOps чат. Конкретные prompts, file ownership, dependencies и gates находятся в плане. Независимые FNS/EIA/PDF review могут выполняться субагентами; общие models/CLI/contracts/agent state пишет один интегратор. Не выделять12микросервисов по названиям12критериев.

DevOps PR7 владеет root compose, Dockerfiles, networks/proxy/env/volumes/bootstrap/smoke и frontend/web/backend-bindings.js. Его существующий /v1/materials proxy и auth расширяются, а не заменяются новым конкурирующим transport. Отчёт PR7: synthetic smoke2 пройден, real generation требует ключа/корпуса и не заявлена. D03/D04 дают команды/health/catalog/provenance. Backend/agent semantic правки требуют владельца соответствующего lane; не переписывать teammate работу. Нет автоматического merge/publish/deploy shared branches, рассылки сообщений сокомандникам, копирования raw/keys вGit, paywall bypass, большой платной оценки до budget gate. Новая архитектура не обещает полную семантическую кластеризацию всех событий или универсальное покрытие любых отраслей.

## 8. Завершение

Код, README и source count недостаточны для отметки «полностью готово». Release manifest фиксирует git/prompt/schema/corpus/profile/source policy versions, original URLs, page review/gold IDs, run/insight IDs, ручные verdicts и фактические gates. Мурад принимает материалы/поиск; Ангелина — корректное использование; Левон — Java/lifecycle/storage; Тимур — смысл/полезность/UX; другой участник — воспроизводимость. BLOCKED/NOT_RUN не превращаются вPASS. Код полностью reviewable в отдельных ветках, данные/секреты остаются внеGit.
