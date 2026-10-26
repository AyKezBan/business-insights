# D04: открытые источники и план завершения ingestion

Проверено **26 октября 2026, Europe/Moscow**. Небольшие HTTP-пробы выполнены 25.10.2026 21:06–21:13 UTC, то есть 26.10.2026 00:06–00:13 МСК. Это исследование: продуктовый код, зависимости, рабочие корпуса и настройки не менялись; LLM и бюджетные endpoints не вызывались.

## Вывод для решения

В `develop=e105b7c` существует рабочая инфраструктура импорта, версий и snapshots, но автоматический импорт внешних новостей подключён только к **одному издателю — Банку России**. В `ml-finalize=416f24f` появился полезный поиск показателей и пресс-релизов того же издателя во время анализа. Он не добавляет новых независимых издателей в постоянный корпус.

Рекомендуемый следующий объём — **три независимых издателя с разными предметными областями**: ЦБ (финансы), ФНС (налоги и обязанности бизнеса), EIA (энергетика и международный сырьевой контекст). У всех есть непустой доступный материал и опубликованные условия использования. Для ФНС и EIA следует использовать проверенные разрешённые HTML-страницы: RSS ФНС сейчас пуст, RSS EIA запрещён robots. EIA применим только при реальной энергетической/внешнеторговой связи компании; американская статистика не становится фактом о российском предприятии.

**Это предложенный объём, не правило хакатона.** В `task/DOMAINS_AND_OWNERS.md`, D04, подготовительное задание прямо предлагает выбрать один внешний источник для MVP. Пользовательский запрос на расширение и реальное тематическое покрытие обосновывает следующий шаг. Три endpoint ЦБ не являются тремя издателями. Даже три издателя не закрывают всё отраслевое/региональное покрытие.

Московская биржа — необязательный дополнительный поток **заголовков и оригинальных аннотаций**, с явной пометкой неполноты. ЕЭК даёт особенно ценный российско-евразийский торгово-промышленный контекст, но общие права повторного использования её сайта пока не подтверждены: не включать её в обещанные три готовых источника, пока это не решено. Правительство, Росстат и ФАС нельзя объявить рабочими адаптерами на основании индекса поисковика.

## Проверенные границы существующего кода

Ссылки на код ниже относятся к неизменным копиям в `generated/finalization-research-2026-10-26/{develop,ml-finalize}`. Основной Git-репозиторий — `business-insights`.

| Узел | Что уже есть | Что предстоит для нескольких источников |
| --- | --- | --- |
| `ml-service/src/insight_agent/materials/sources.py` | `NewsSource.fetch(limit) -> list[RawNews]`, минимальный Protocol | Реестр разрешённых source IDs, тип результата со статусами и ошибками отдельных статей; протокол пока не используется для выбора источника CLI |
| `materials/news.py` | `CbrRssSource`; RSS 2.0, DTD/entity guard, URL allowlist только `cbr.ru/www.cbr.ru`, ограничение 2 MiB, timeout 10 с, ограниченные redirects; `.landing-text` для ЦБ | Адаптеры ФНС/EIA, их HTML-discovery и отдельные article selectors; общий контролируемый transport. Простое расширение allowlist не создаёт адаптер |
| `materials/cli.py` | `news --source cbr` либо `--saved`; 1–100 статей; единый writer lock; merge и atomic publish; ошибка сохраняет старый snapshot | Выбор нескольких source IDs; source-by-source результаты; различение `empty`, `partial`, `blocked`, `unavailable`; HTTP сейчас выполняется под writer lock |
| `materials/models.py` | `RawNews` хранит URL/title/text, `published_at` с timezone или `None`, `retrieved_at`; `SourceVersion` хранит текст/hash/версию/event/ref times | Нет `publisher_id`, feed/source key, способа/версии extraction, вида материала, режима прав, точности даты, исходного URL до canonicalization |
| `materials/dedup.py` | Удаление tracking-параметров с сохранением смысловых query (`file`); exact-text объединение разных URL с сохранением refs; новые версии исправлений; защита от отката поздней исторической записью; ручной `link` | Междоменное canonicalization только по проверенным правилам; provenance издателя; не добавлять автоматическую semantic-дедупликацию как условие расширения |
| `materials/store.py` | Проверка hashes/refs/active versions; immutable snapshot и atomic `current.json`; serving закрепляет revision | Статус сбора по каждому издателю и отдельный ingestion-run report; политика частичного успешного сбора; сохранение failed-source прежних записей |
| `materials/api.py` | Доступ к исходным банковским PDF | Нет источникового health/status API и HTTP-import API; существующий импорт — CLI. Новый ingestion API для MVP необязателен |

`docs/materials-acceptance.md` фиксирует 12 реальных статей ЦБ и A07 на двух URL в unit-тесте. Это доказательство пригодности механизма source refs, а не подключения двух реальных издателей. Его результаты 115 тестов — историческая приёмка 24.10, здесь тесты не перезапускались и не выдаются за свежую проверку.

### Не дублировать работу `ml-finalize`

`research/fetch.py` уже содержит `SafeFetcher` с HTTPS/host allowlist/ограничением размера и redirects. `research/service.py` содержит `OpenResearch`, TTL cache, key-rate/FX/press шаги с независимой обработкой ошибок. `research/indicators.py` разбирает числа через `Decimal`, различает даты значений и получения. В `agent/orchestrator.py` инструмент `search_open_sources` строит план по профилю и регистрирует найденный текст в runtime `NewsStore`.

Это **request-time enrichment**, а не регулярная загрузка нескольких издателей: hosts по умолчанию только ЦБ; CLI материалов по-прежнему знает только `cbr`; нет `merge_news/publish_snapshot` из research. Не переписывать этот модуль вторым конкурирующим сервисом. Согласовать общий transport и provenance, а новые источники подключать к имеющемуся materials pipeline. Существующий `_as_news_event` превращает и `kind="indicator"` в `SourceKind.news` и дату без времени — в UTC midnight: это место для явного разграничения material kind/date precision, а не доказательство появления новой новости.

## HTTP-проверка и бизнес-покрытие

Статусы ниже — наблюдение из этой среды и в указанное время, не обещание uptime. TLS-проверка оставалась включённой, авторизация/cookies/paywall не обходились. На feed читалось не более 128–192 KiB и сохранялись только 2–3 item; на кандидата прочитана одна небольшая публичная статья. Целые архивы не скачивались.

| Издатель / режим | Проверенный discovery и результат | Проверенная статья / дата | Покрытие и ограничения |
| --- | --- | --- | --- |
| **ЦБ; полный первичный пресс-релиз** | [RssPress](https://www.cbr.ru/rss/RssPress): **200**, RSS, 80 437 B, непустой | [Решение по ставке](https://www.cbr.ru/press/PR/?file=23102026_133000key.htm): **200**, 39 865 B; feed `23 Oct 2026 13:30:00 +0300` | РФ; стоимость долга, валютная/финансовая политика, финансовые контрагенты. Пресс-релизы надзора часто нерелевантны обычному МСП. [Официальный список RSS](https://www.cbr.ru/development/rss/) |
| **ФНС; полный HTML, RSS сейчас непригоден** | [RSS](https://www.nalog.gov.ru/rn77/rss/): **200**, 604 B, **0 item**, повторная проверка тот же результат. [Разрешённая HTML-рубрика](https://www.nalog.gov.ru/rn77/news/activities_fts/): **200**, 97 798 B, непустая | [Срок авансовых платежей организаций](https://www.nalog.gov.ru/rn77/news/activities_fts/16664106/): **200**, 71 824 B; на странице `23.10.2026 09:30`, offset отсутствует | РФ, налоги/сроки/отчётность/регистрация, все отрасли. `rn77` — выбранный регион, а не гарантия применимости всей записи только к Москве; федеральные и региональные материалы различать по содержанию/тегам |
| **EIA; полный собственный текст HTML** | [Today in Energy](https://www.eia.gov/todayinenergy/): **200**, 75 784 B, непустые ссылки `detail.php?id=...`; RSS не запрашивался из-за robots | [Статья о загрузке НПЗ](https://www.eia.gov/todayinenergy/detail.php?id=68344): **200**, 51 194 B; `October 22, 2026`, **только день** | США и международная энергетика: нефть/газ/электроэнергия, supply/demand, топливо и экспорт. Это собственная публичная аналитическая публикация, не российская локальная новость и не оценка Альфа-Банка. Не извлекать платные сторонние изображения/наборы данных |
| **MOEX; заголовок + publisher annotation** | [Официальные RSS и условия](https://www.moex.com/a40); [cat=100](https://www.moex.com/export/news.aspx?cat=100): **200**, непустые item, чтение ограничено 192 KiB | [n105397](https://www.moex.com/n105397): **200**, 68 016 B; RSS `23 Oct 2026 19:24:28 +0300` | РФ, торги/эмитенты/листинг/инструменты. У первых двух item `description` — 199/203 символа, `content:encoded` — 1922/806: аннотация действительно отличается от полного текста и может обрываться внутри таблицы. Подробности за пределами аннотации недоступны анализу |
| **ЕЭК; технически рабочий HTML, права требуют решения** | [Новости](https://eec.eaeunion.org/news/): **200**, 123 977 B; RSS/feed paths запрещены robots и не запрашивались | [Меры в отношении импорта](https://eec.eaeunion.org/news/eaes-zashchitit-svoyu-promyshlennost-ot-vozrosshikh-obemov-importa/): **200**, 97 036 B; `22.10.2026`, только день | ЕАЭС: РФ/Беларусь/Казахстан/Армения/Кыргызстан; пошлины, стандарты, промышленность, АПК, логистика. Особенно полезно импортёру/производителю. Отдельно хранить дату публикации и условия вступления меры в силу |

У EIA собственная статья и у ЕЭК официальное сообщение — первичные публикации их авторов. Они не являются «позицией банка» и не должны попадать в `bank_assessment`. EIA содержит описания других источников: ограничить первую интеграцию собственным текстом и публикациями с первичными EIA data-source labels, не копировать внешние datasets/графику.

### Кандидаты, которые не следует обещать готовыми

| Кандидат | Фактическая проверка | Решение |
| --- | --- | --- |
| Росстат | `https://rosstat.gov.ru/robots.txt` и `https://www.rosstat.gov.ru/robots.txt`: `CERTIFICATE_VERIFY_FAILED`, issuer не доверен стандартной цепочке этой среды; свежая web-загрузка [рубрики](https://rosstat.gov.ru/folder/313) дала 502/timeout | Сильный будущий источник РФ: выпуск CPI/промпроизводства/розницы — публикации; временные ряды — enrichment. Сначала корректная доверенная TLS-цепочка в целевой среде и robots/terms; `verify=False` недопустим |
| Правительство РФ | HTTPS robots timeout; свежие [all/rss](https://government.ru/all/rss/) и `/about/` через web тоже timeout | Широкое покрытие политики/поддержки/отраслей, но ни свежий feed, ни текущие reuse-условия не подтверждены. Не опираться на cached главную или предполагаемую CC-лицензию |
| ФАС, Минэкономразвития, ФТС | HTTPS robots ФАС (`fas.gov.ru` и `www.fas.gov.ru`), `www.economy.gov.ru`, `customs.gov.ru` timeout | Резерв для competition/tariffs/customs/SME после повторной проверки из среды запуска; статьи не собирались при недоступной robots |
| Минфин | robots **200**; содержит `Disallow: /` для `ChatGPT-User`, `GPTBot`, `openai`, `openai.com`; web press-center вернул 503 | Не продолжать сбор другой идентичностью; не включать в текущий портфель |
| РБК | Feed технически **200**, но чтение ограничено; после проверки [официальных правил](https://www.rbc.ru/privacy/) исключён. Точное условие: «Запрещается автоматизированное извлечение информации сайта любыми сервисами без официального разрешения АО „РОСБИЗНЕСКОНСАЛТИНГ“» | Не предлагать автоматический scraper/RSS-to-full-text как свободно разрешённую интеграцию. Условия RSS/robots сами по себе не отменяют это правило |
| Интерфакс | [rss.asp](https://www.interfax.ru/rss.asp) → `/rss`, **200**, 20 891 B, непустые короткие descriptions; robots не запрещает обычные страницы. [Правила](https://www.interfax.ru/license), п.3: «в том числе через RSS рассылки, полностью запрещается Интерфаксом» в контексте указанного в пункте способа воспроизведения | Правила отдельно допускают ограниченное цитирование с атрибуцией и договорное использование. Это не разрешение на произвольный полный ingestion/переиздание. Не подключать turnkey full-text source без подходящего права использования |
| Коммерсантъ | [news.xml](https://www.kommersant.ru/RSS/news.xml) → `/rss/news.xml`, **200**, непустые descriptions; ETag/Last-Modified. Robots запрещает `/doc-rss`, `/rules`, `/api/` и ряд других маршрутов | Наличие RSS не подтверждает общий reuse grant; полный текст и paywall не проверялись. Не использовать `/doc-rss` как обход и не считать готовой заменой официальным источникам |
| MOEX ISS | [robots](https://iss.moex.com/robots.txt): **200**, `Disallow: /` | API не вызывался. Рыночные котировки — отдельные данные/условия доступа, не новостной издатель; не путать с разрешёнными аннотациями сайта MOEX |

Это описание текущих опубликованных условий и ограничений проверки, а не юридическое заключение о допустимости любого будущего способа использования.

## Extraction, даты, URL и права

Селекторы ниже **наблюдались на указанных образцах**, а не доказаны для всех исторических шаблонов. Не использовать общий `body.get_text()` и не считать непустой navigation текст успешной статьёй.

| Издатель | Discovery / article extraction | Даты и canonical URL | Robots, права, нагрузка |
| --- | --- | --- | --- |
| ЦБ | RSS item link/title/pubDate; статья `.landing-text`; текущий CBR extractor уже существует | Сохранять offset RSS и UTC-нормализацию; `published_at` отдельно от `retrieved_at`. На образце нет `rel=canonical`; final HTTPS URL с **обязательным `file=`** остаётся source locator | [robots](https://www.cbr.ru/robots.txt) не запрещает проверенные пути; `/search/` запрещён. [О сайте](https://www.cbr.ru/about/) разрешает воспроизведение с ссылкой, содержит также условия использования ссылок/ресурсов. Не представлять собственный ресурс как сайт ЦБ |
| ФНС | `.news-block__item .news-block__name a` + `.news__time` в рубрике; статья `div.text_block`, метка `.single-news__time`. Удалять `.share`, формы, scripts, скрытые элементы; брать содержательное тело до служебных блоков. В образце вложенные `<p>`: не склеивать тексты всех вложенных p повторно | Сохранять raw `23.10.2026 09:30`; timezone в образце не объявлена. `Europe/Moscow` может быть явно согласованной настройкой источника, но не фактом из HTML. До решения не подменять неизвестное время точным UTC. Canonical tag отсутствует: проверенный final URL | [robots](https://www.nalog.gov.ru/robots.txt) разрешает `/rn77/news/activities_fts/`, запрещает `/rn*/rss_region/` и search. [Условия](https://www.nalog.gov.ru/rn77/copyright/) разрешают бесплатное воспроизведение с интерактивной ссылкой, без предварительного изменения; Internet-сервисы исключены. Raw сохранять отдельно, AI-вывод маркировать отдельно |
| EIA | `.tie-article h1 a[href*='detail.php?id=']` на recent index; `.tie-article`, `.date`, `h1`, содержательные p в статье; не захватывать общий footer, «Tags» и «More recent articles». `Principal contributor` — metadata | Только publication day; timezone/час не объявлены. `og:url` соответствует final `https://www.eia.gov/todayinenergy/detail.php?id=68344`; **`id=` обязателен**. Не заменять дату получения публикацией, не изобретать точный midnight | [robots](https://www.eia.gov/robots.txt) разрешает `/todayinenergy/`, запрещает `/rss`, archive-пути и ряд других. [Copyrights and Reuse](https://www.eia.gov/about/copyrights_reuse.php): собственные гос. публикации доступны для использования с acknowledgment источника/даты; перевод отмечать отдельно и ссылаться на оригинал; сторонние фото/материалы и logo имеют отдельные ограничения. Не использовать RSS несмотря на [каталог feeds](https://www.eia.gov/tools/rssfeeds/) |
| MOEX | RSS title/link/pubDate/**description**; не импортировать `content:encoded` и HTML body в режиме `publisher_annotation`. Короткая/оборванная таблица → ограниченные факты или отказ, не восстановление недостающего текста | RSS offset +0300. HTML имеет относительный `rel=canonical href='/n105397'`; разрешённо разрешить относительно того же HTTPS host. `og:url` на образце указывает просто главную и **не должен** заменять canonical | [robots](https://www.moex.com/robots.txt): **Crawl-delay: 10**, отдельные запрещённые `/n...`; проверять каждый URL. [RSS page](https://www.moex.com/a40) разрешает размещать заголовки/аннотации, не обещает общий full-text режим |
| ЕЭК, после решения по reuse | `a.NewsItem`, `.NewsItem__Title`, `.NewsItem__Date`; article `.NewsDetailTitle`, `.NewsDetailInfo__Date`, `.DetailBlock._slim`. Удалять изображение/UI, не только p: первый содержательный абзац находится **текстовым узлом вне p** | Только `22.10.2026`; timezone/время неизвестны. Canonical tag отсутствует: final HTTPS article URL. Публикация новости ≠ официальная публикация нормативного акта ≠ вступление в силу | [robots](https://eec.eaeunion.org/robots.txt) разрешает проверенные `/news/` и статью, но запрещает `*/feed`, `*/rss` и один конкретный news URL. На news/footer и [press-center](https://eec.eaeunion.org/press-center/) общего reuse grant не найдено; ©/доступность не равны лицензии. Не заявлять свободную лицензию и не отправлять пресс-службе запрос без отдельной команды пользователя |

У ФНС текст «Это архивная публикация» есть в HTML внутри `style="display: none"`. Общий text extractor может ошибочно объявить текущую статью архивной. Проверять видимость/семантику, а не наличие строки в исходном HTML.

Числовая публичная квота для ЦБ/ФНС/EIA/ЕЭК в проверенных страницах не найдена: **это не означает unlimited**. Предлагаемый консервативный режим MVP — один запрос одновременно на host, небольшой per-source cap, опрос примерно раз в час с cache/conditional GET, если источник предоставляет validators. MOEX — не чаще указанного crawl-delay между запросами. `429/Retry-After` переносит попытку; не крутить бесконечные retry. Это настройки проекта, не официальные лимиты источников.

## Новость и enrichment: сохранить различие

| Материал | Откуда | Как использовать |
| --- | --- | --- |
| Оригинальная публикация о событии | ЦБ, ФНС; собственная статья EIA; ЕЭК после решения по reuse | `NewsEvent` с конкретным опубликованным текстом/датой/URL/version; EIA помечать как public analysis, forecast сохранять как forecast |
| Оригинальная аннотация | MOEX RSS description | `material_type=publisher_annotation`, `completeness=partial`; утверждения только из доступного отрывка, UI/trace показывают ограничение |
| Ключевая ставка / официальный валютный курс | [CBR XML documentation](https://www.cbr.ru/development/SXML/), key-rate table | `indicator`, числовой факт + единицы + effective date + retrieval + URL запроса/параметры. Не отдельный издатель и не новая статья |
| Временные ряды CPI/промышленности/розницы, market quotes | Росстат / MOEX ISS после разрешённых transport/rights решений | Отдельный typed indicator adapter. Выпуск официального сообщения и dataset — разные материалы; не подменять новостное покрытие числом series |

Независимо проверены **200** для `XML_daily.asp?date_req=23/10/2026` (9514 B, `ValCurs`, windows-1251) и key-rate table с датами 23.10.2026 (30 499 B, table). Это подтверждает доступность endpoint, а не перепроверяет все вычисления teammate. Сохранить сделанный им `Decimal` parsing/cache, не строить второй аналог.

## Контракт расширения и семантика сбоев

Предлагаемый registry: фиксированные `cbr_press`, `fns_news`, `eia_today`, опционально `moex_annotations`; `eec_news` disabled/pending до решения прав. Для каждого: `publisher_id`, разрешённые hosts/paths, discovery URL/type, selectors/parser version, date policy, `material_type`, rights URL/режим, poll cap/cache, reviewed-at. Несколько feeds одного publisher не увеличивают publisher count.

Результат адаптера должен содержать `records`, `source_id`, `status`, `attempted/succeeded/rejected`, `item_errors`, check time и reason code. Совместимость можно сохранить вокруг имеющегося `NewsSource.fetch`, добавив collector, но не глотать ошибки и не возвращать `[]` при любом исключении.

1. Fetch каждого издателя независим; сбор не требует LLM. Сначала собрать кандидатов, затем под writer lock **снова загрузить последний snapshot**, выполнить merge и одну atomic публикацию. Так HTTP не держит lock, а параллельный importer не теряет обновления.
2. Один источник не отвечает → успешные источники публикуются, старые записи недоступного источника остаются. Run помечается `partial`, source status указывает последнюю удачную загрузку. Это не `healthy` и не пустая лента.
3. Одна статья 404/сломанный selector → остальные item этого издателя продолжаются; failed item фиксируется. Feed 200 с 0 item, как ФНС, — `empty`, а не полноценный успех; только заранее разрешённый fallback discovery.
4. Все источники недоступны, все записи невалидны либо общий snapshot не прошёл валидацию → pointer не меняется, exit nonzero, отчёт хранит причины. Штатно нет новых материалов/304 → `unchanged`, counts не становятся нулём.
5. 401/403, robots/rights denial, challenge/paywall → `blocked`, без альтернативного User-Agent, cookies или скрытого endpoint. RSS description можно использовать только если режим и права разрешают его как ограниченную аннотацию; не называть её полным текстом.
6. Не использовать pending-source ошибки как `snapshot.report.errors` для отмены всего полезного batch: отделить ingestion-run health от ошибок целостности publishable corpus. Не включать изменчивые health/check timestamps в content identity.

### Idempotency и provenance

Существующие exact-text merge, source history, активная версия и semantic query preservation сохраняются. При повторе **того же сохранённого batch** IDs/counts/revision стабильны. При новом live-получении неизменного текста `merge_news` обновляет `SourceVersion.retrieved_at`; этот timestamp входит в snapshot hash, поэтому revision может измениться без нового event/version. Не обещать стабильный live revision, не решив отдельно, будут ли observation timestamps жить в run-report/sidecar.

Требуемая идентичность — стабильные event/source-version IDs и отсутствие дублей по неизменному normalized content+URL+publication metadata. Исправление по прежнему URL создаёт version и сохраняет историю; старый поздний импорт не становится active. Нельзя считать разные числа/отрицание безопасным дублем. Federal article ФНС, доступный через разные `rnXX`, — одна история одного publisher, а не независимое подтверждение; первоначально выбрать один регион и не собирать все региональные зеркала.

Provenance хранит publisher/adapter ID, discovered URL, final/canonical URL, raw publication value и precision/timezone source, retrieval timestamp, content hash/version, parser version, `material_type`, completeness, rights URL/decision и source-specific tags. Внешний `SourceRef.url` остаётся оригинальным locator. Добавлять metadata обратно совместимо и синхронно обновлять Pydantic/schema export/потребителей; не ломать старые saved batches.

## Исполнимый backlog

| Приоритет / владелец | Работа и конкретные файлы | Проверяемый результат |
| --- | --- | --- |
| P0, D04 Мурад + D11 Ангелина | Согласовать общие transport/provenance с `research/fetch.py`; registry в `materials/sources.py` или небольшом `materials/registry.py`; конфигурация fixed source IDs | Не возникает второго request-time researcher; разрешения hosts/paths индивидуальны; нет произвольного URL из LLM/user |
| P0, D04 | Добавить `materials/fns.py` с HTML fallback; `materials/eia.py` с recent HTML и original-text extraction; сохранить `CbrRssSource` | Непустые normalized records с исходными title/date/URL, видимый текст без меню/форм; FNS пустой RSS не объявляется успехом; EIA RSS не вызывается |
| P0, D04 + D07/D12 | Расширить `materials/models.py`, `schemas.py`, schema export/контракты только необходимыми metadata; date precision не выдумывает часы | ФНС naive datetime и EIA day-only воспроизводимо сохранены; cutoff policy явная; publisher count считается по registry, не домену/source-ref count |
| P0, D04 | Multi-source collector + `materials/cli.py`, run-report; `store.py` только для целостности/atomic publish | Один failing источник не отменяет хорошие записи; весь failed batch не повреждает pointer; вывод CLI содержит per-source status/counts, а не только общий news total |
| P0, D04 + D01/D02 | `classify_topics` и mapping source tags | Налоговые сроки не остаются без категории; английское `refinery utilization rates` не становится `cost_of_debt` из-за единственного слова `rate`; EIA география/forecast не теряются |
| P0, D04 + D07 | Обновить `ml-service/docs/material-ingestion.md`, `config` source registry, README/Compose запуск подготовки | Другой участник делает ограниченный сбор из трёх издателей; restart serving применяет revision; `/health` и corpus counts отличают serving snapshot от последнего import attempt |
| P1, D04 | `materials/moex.py` в режиме annotations; запись material kind/rights/completeness | Только title/description; `content:encoded` не импортируется; краткая аннотация не поддерживает отсутствующие в ней ticker/числа |
| P1, D04/Product | Решить reuse ЕЭК по опубликованным условиям/полученному разрешению, затем `materials/eec.py` | Trade/industrial lane включается только с зафиксированным основанием; запрещённые RSS/отдельные paths остаются запрещёнными |
| P1, D04/D07 | Повторная проверка Росстата/Правительства/ФАС из deployment environment | Честно обновлённый source status, доверенный TLS и актуальные robots/terms; нет отключённой проверки сертификата |

Оценку ёмкости/сроков должен дать исполнитель после выбора metadata migration; порядок работ выше не является обещанием числа часов.

## Приёмка источников без LLM-вызовов

Это предложенные проектные проверки, а не условия организаторов. Существующие `test_news_source.py`, `test_material_cli.py`, `test_news_dedup.py`, `test_material_store.py` расширяются по смыслу; новые adapter fixtures — маленькие synthetic/разрешённые extracts с URL/date/hash metadata, без полных copyrighted статей в Git.

| Проверка | Ожидаемое доказательство |
| --- | --- |
| Три издателя, не три ЦБ endpoint | Ограниченный real smoke: по одному полноценному разрешённому материалу ЦБ/ФНС/EIA; три distinct publisher IDs, три сохранённых исходных locator, hash/version и content-kind. Новая статья проходит CLI → published snapshot → `NewsStore`/`InsightRequest` без правки кода/fixtures |
| FNS пустой RSS | Mock точных 604-byte XML + HTML index: `empty` распознан, только разрешённый HTML fallback даёт records; `/rss_region/` не запрашивается |
| Extraction fidelity | Числа/проценты/единицы/отрицания/условия из маленьких gold extracts сохранены; hidden FNS archive notice и feedback отсутствуют; EEC первый абзац вне p сохраняется, если source разрешён; EIA footer не входит в текст |
| URL identity | CBR `file=one` и `file=two` различаются; EIA `id=68344` сохраняется; tracking removal не стирает смысловой query; canonical на чужой host/главную отвергается; FNS региональные зеркала не изображают independent publisher |
| Даты и cutoff | Offset RSS нормализован; missing/naive/day-only не заменены retrieval временем; внутридневной historical cutoff не использует day-only/unknown-time статью как гарантированно уже опубликованную; effective date показателя и вступление правовой меры не равны news date |
| Три source failures | У одного timeout, у второго одна статья 404, третий успешен → partial publish, живые records сохранены, failed-source old refs остаются; all-fail и corrupted snapshot не меняют `current.json` |
| Rate/robots/rights | Mock 429/Retry-After не вызывает loop; MOEX запросы соблюдают 10 с без медленных unit sleeps через fake clock; EIA `/rss`, EEC RSS/feed, MOEX ISS и запрещённый article URL не вызываются; redirect за allowlist/в приватный host отклонён |
| Idempotency/corrections | Одинаковый saved batch ×2: те же IDs/counts/revision; fresh observation unchanged content: та же event/version identity и оговорённая revision policy; исправление числа по прежнему URL → новая version, прошлый текст доступен; historical late observation не откатывает current |
| Provenance в потребителях | Original URL/hash/publisher/material kind проходят snapshot → API/trace; annotation отображается как неполная; indicator не выдаётся за новую публикацию и public analysis EIA не выдаётся за bank assessment |
| Релевантность расширения | Профиль с налоговой обязанностью получает ФНС lane; energy/import exposure — EIA только с объяснимой связью; отсутствие связи не компенсируется выдуманным влиянием. Семантическая/LLM приёмка — отдельный этап владельцев D02/D05/D11 |

## Локальные свидетельства

Пробные результаты и маленькие образцы находятся вне Git-репозитория:

`/Users/selebry/Documents/dev/alpha-hack/generated/finalization-source-probes-2026-10-26/`

Машиночитаемые файлы: `robots-outcomes.json`, `reserve-robots-outcomes.json`, `feed-outcomes.json`, `article-outcomes.json`, `article-extra-outcomes.json`, `indicator-outcomes.json`, `eia-robots-outcomes.json`, `eia-index-outcomes.json`, `eia-article-outcomes.json`. В `feed-outcomes` первая диагностическая сборка отдельных namespace-bearing RSS item дала `unbound prefix` для MOEX/RBC: это ошибка исследовательской упаковки item без root namespace, **не поломка feeds**. MOEX повторно сохранён с namespace и успешно разобран; RBC после чтения условий дальше не исследовался. Ограниченный ответ не выдаётся за полное содержимое feed.

Полные статьи в этот отчёт не скопированы. Исследовательские файлы не являются production-адаптерами или импортированным демонстрационным корпусом. Отсутствие Ruflo MCP в доступном tool catalog не заменено другой внешней системой: публичные источники проверены напрямую read-only.
