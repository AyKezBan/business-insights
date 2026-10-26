# Карта чатов, субагентов и передачи D03/D04

Дата:26.10.2026. Основание: [spec](../specs/2026-10-26-domains-03-04-finalization-design.md), [план T01–T12](2026-10-26-domains-03-04-finalization.md). Это распределение будущего исполнения после согласования, а не уже созданные implementation chats. Исследование завершили три read-only субагента; новый spec проверил четвёртый.

## Уже существующие чаты

| Чат | Статус и роль |
| --- | --- |
| «Исследовать материалы и спланировать» — текущий | Координатор C0: спецификация, общий контракт, integration base, T01/T02/T07, итоговая сверка и PR |
| «Общий Docker Compose и DevOps business-insights» | Создан по прямому поручению пользователя. Завершена отдельная реализация, draft PR7, headb177c7d, feature/full-stack-compose. Общий Compose, nginx/bindings, четыре образа, два synthetic smoke. Не подтверждает live A10 |

DevOps чат ID: `01a174e1-4e8b-7be1-b329-dbe6ac570f85`. PR: https://github.com/AyKezBan/business-insights/pull/7 . Общий запуск из alpha-hack: `./stack up`; из repo/worktree — собственный `./stack`. Конфигурация реальных CSV/PDF/ключа требуется отдельно, ссылки/инструкция находятся в PR7. Не запускать compose down -v против существующих данных для «проверки».

## Три реализационных чата

### C1. D04 — независимые источники и синхронизация

**После:** committed T02 interface/base. **Задачи:** T03/T04/T05. **Предлагаемая ветка:** `feature/materials-publishers-final` от указанного coordinator SHA.

**Субагенты:**

1. FNS adapter: только `materials/adapters/fns.py`, `tests/test_fns_source.py`, свой fixture prefix.
2. EIA adapter: только `materials/adapters/eia.py`, `tests/test_eia_source.py`, свой fixture prefix.
3. После объединения — независимый reviewer failure/idempotency/rights/date cases.

Каждый implementer получает отдельный worktree и index. Общие registry, models, CLI, classifier, transport и schema пишет интегратор C1/C0 по одному владельцу файла. Субагенты не меняют их в обход протокола. Сначала передать source commits/fixtures и sample metadata, затем интегрировать collector; совпадение signatures проверяется до cherry-pick/merge.

**Передача:** по одному реальному допустимому material каждого publisher, hashes/URLs/date precision, run reports, statuses/partial/all-fail/replay/correction, совместимость v1 и точные команды. Нельзя считать несколько CBR endpoints или сохранённый вручную fixture новым издателем.

**Готовый prompt для нового чата:**

> Исполни T03–T05 плана `docs/superpowers/plans/2026-10-26-domains-03-04-finalization.md` и spec по закреплённому INTEGRATION_BASE, который передаст координатор. Работай в своей ветке/worktree. Переиспользуй T02 SourceSpec/FetchBatch и общий transport. Два независимых субагента реализуют ФНС HTML и EIA permitted HTML; общий collector/CLI интегрирует один владелец. Цель — три действительных издателя CBR/FNS/EIA, корректные даты/типы/условия/units, источник не пропадает при частичном отказе. Сначала meaningful RED, затем GREEN, small real source smoke без LLM. Не использовать EIA RSS, FNS rss_region, paywall/TLS/robots bypass. Не редактировать agent/Java/UI/Compose и не отправлять сообщения сокомандникам. Верни reviewable commits, reports и оставшиеся blockers; raw articles/keys внеGit.

### C2. D03 — банк, review и поиск

**После:** T02 совместимости; параллельно C1. **Задача:** T06; помощь T10 по проверенным spans. **Ветка:** `feature/bank-evidence-final` от закреплённого integration SHA.

**Субагенты review:** непересекающиеся physical-page группы, задаваемые manifest. Возможное распределение64страниц: workerA первые21страницу набора (пять небольших PDF), workerB FI strategy p1–21, workerC остальные22страницы. Перед запуском coordinator проверяет сумму и единственность `(doc_id,page)`; конкретная группировка может быть уравновешена без смены gate.

Агенты используют PDF skill и реальные изображения. Каждый пишет свой локальный review file. Они не перезаписывают raw/OCR и не коммитят private text/images; hash-bound decisions и page dispositions объединяет один reviewer. Supersedes между документами принимает root C2 после проверки обеих версий, не отдельный worker вслепую.

**Передача:**64page dispositions; целевое покрытие30verified/6dated docs/6тем, точные hashes и horizons; если данных недостаточно — объяснённый gap, а не фиктивные verified. Старый gold10 и независимый extension12/near-negatives6. PDF/source locator contract совместим с T07.

**Готовый prompt:**

> Исполни T06 плана финализации D03/D04 от координаторского INTEGRATION_BASE. Входные PDF `/Users/selebry/Documents/dev/alpha-hack/data/pdf`, прежние immutable OCR/corpus `/Users/selebry/Documents/dev/alpha-hack/generated/business-insights-materials-2026-10-24-a3e5e14`. Проверь v1 через новый loader. Раздели64реальные страницы между read-only review субагентами без пересечений. Визуально проверь будущие bank claims; изменённый OCR сохраняй новой версией. Old steel/undated Gold/expired forecasts не повышать до current. Не делай массовый verified. Независимый gold фиксируется до tuning; BM25 сохраняется, измеренный дефект воспроизводится regression. Агентный цикл, новые publishers, Java/UI/Compose не менять. Верни review metadata, tests/gold результаты и границы покрытия без полного частного текста вGit.

### C3. Интеграция evidence и совместная приёмка

**После:** committed T01/T02; source/runtime gates по зависимостям. **Задачи:** T08/T09/T10/T11/T12 с владельцами Java/ML/UI. **Ветка:** `feature/material-evidence-integration`.

T08/T09 пишет один Java implementer последовательно. T10 — один ML implementer после выдачи общих schemas C0; он не меняет registry/CLI. T11 расширяет `frontend/web/backend-bindings.js` и proxy PR7, а не создаёт второй. Одновременная запись Java DTO/schema/state/proxy двумя чатами запрещена. T07 runtime остаётся C0; его interface commit обязателен до интеграции consumers.

**Субагенты:** contract/JSONB tests; adversarial source-semantic cases; независимый финальный whole-branch review. Параллельные read-only проверки допустимы. В конце один согласованный integrated head проходит настоящую disposable DB/browser приёмку и budget-gated live; synthetic HTTP/LLM нельзя выдавать за неё.

**Передача:** token-aware catalog CAS и замороженные operation inputs; старое QA/PDF на старой revision; полный provenance в Java/UI; source→карточка→original→QA→trace;9новых пар+9QA с независимой рубрикой; подтверждение второго участника. Мурад не подписывает смысловую приёмку от лица Тимура/Ангелины.

**Готовый prompt:**

> По spec и T08–T12 интегрируй материалы в актуальную Java/ML/UI систему, согласуя protocol commits с C0. Используй готовый DevOps PR7/bindings; не сливай shared branches или чужие PR без команды. Java должна сохранять SourceRef/claim/outcome/profile/time context, корректно обновлять trusted catalog по полному CatalogToken и замораживать event/profile/corpus до очереди. Нельзя лечить stable event409 простым upsert. ML/QA привязаны к revision, semantic defects в actor/negation/time/unit/bank attribution получают failing tests и безопасную обработку тем же verifier/budget. Работай по одному владельцу общих файлов, в отдельных worktrees для независимых задач. Release не объявляется полной до фактических DB/browser и G09 live ворот; до live проверь общий лимит и отдельный session cap. Код, fixtures, инструкции и отчёт должны быть reviewable; private data/keys/полные generated outputs не публикуй.

## Координаторский протокол

1. Согласовать spec/план; зафиксировать функциональный scope, портфель и budget cap. Это единственный продуктовый gate перед новыми D03/D04 changes; метод чаты+субагенты уже выбран.
2. C0 делает T01/T02 в `feature/materials-finalization`, сверяет фактические DevOps/ML/UI refs. Работа начинается от свежего develop, не от старого a3e5e14. Планы/reports копируются в новую ветку для Git history.
3. Выдать каждому чату абсолютный путь worktree, INTEGRATION_BASE SHA, список его файлов и закрытые interfaces. При изменении интерфейса — один coordinator commit, затем явно обновить consumers, а не независимые несовместимые правки.
4. C1 и C2 параллельны. C3 готовит tests/DTO после T02; T09 ждёт T07; T10 принимает проверенные spans; T11/T12 ждут объединённый результат. Общие файлы и Git index не разделяются между writers.
5. Каждый handoff содержит commit IDs, actual commands/results, sample metadata, failure semantics, blockers. «Готово в домене» без consumer-проверки не закрывает сценарий.
6. C0 объединяет feature commits в свою integration ветку, проверяет все mandatory gates, выполняет fresh review, создаёт финальный PR к актуальному develop. Отдельные stacked PR допустимы только с явным base/dependency; не merge в remote develop автоматически.
7. Все workflow actions в других пользовательских чатах требуют авторизации пользователя. Этот документ — prompts для разрешённого будущего старта, а не разрешение рассылать сообщения сокомандникам в Slack/email или создавать расписания Codex.

## Что можно делать до согласования

Читать код/источники, уточнять исследования, проектировать interfaces и тестовые критерии. Созданный по отдельному поручению DevOps чат уже вправе выполнять свою задачу; она завершилась PR7. Новые D03/D04 product edits/live-model runs этому исследованию не нужны и пока не выполняются.
