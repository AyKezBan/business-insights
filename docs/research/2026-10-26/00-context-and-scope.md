# Контекст финальной доработки D03/D04 — 26.10.2026

Пользователь просит новое полное исследование после обновлений команды, план финализации D03/D04 с распределением по задачам/чатам/субагентам. Отдельно поручена реализация общего Docker-запуска в новом чате. Исследование и план D03/D04 не являются началом новой реализации этой части.

## Свежий снимок

| Ref / PR | Состояние при fetch26.10 | Что учитывать |
| --- | --- | --- |
| develop | e105b7c | Общая база: data-import, ML, D03/D04, Java уже здесь |
| #2, #3, #4, #5 | MERGED | Старый draft #4 принят25.10; это не подтверждение A10/A13 |
| feature/ml-finalize | 416f24f | Новые contracts/budget/as_of/idempotency/persistence/research/guards/evaluator; требуется reuse |
| #6 UI clarity | OPEN, 7c59dd0 | UI в frontend/, transport boundary есть, реальные DTO bindings нужны |
| исходный checkout | detached cb28d5f | Не переключать/не reset, untracked docs/superpowers сохранены |
| прежний materials worktree | a3e5e14 | Историческая реализация, новая работа начинается от свежего develop |

Полные SHA и проверенные merge trees: [отчёт01](01-branches-integration.md). Анализ выполнен по неизменяемым exports в `generated/finalization-research-2026-10-26/{develop,ml-finalize,ui-clarity}`, без замены текущих checkout. Удаление блока D03/D04 из README в e105b7c не удаляет реализацию материалов.

## Инвентаризация папок

Просмотрены все верхнеуровневые папки: task, chat-export, data, data-analysis, business-insights, docs, а также derived worktrees/generated. Новых исходных CSV/PDF/экспорта чата после24.10 по metadata нет; обновления приходят из GitHub.

- data:9PDF+3CSV; полные CSV не переимпортировались и не изменялись.
- task: кейсPDF, вебинар, правила моделей/ключей, DOMAINS_AND_OWNERS, PLAN-MVP, Meeting-20.
- chat-export: прежний экспорт,76 вложений/ресурсов; data-analysis: notebook и выгрузки.
- docs: прежние семь research/spec audit файлов; это исторические измерения, а не сегодняшний статус сервисов.
- generated/worktrees: локальные производные corpus/eval/snapshots и isolated Git checkouts. Они не являются новым источником бизнес-данных.

Metadata inventory: `generated/finalization-research-2026-10-26/workspace-inventory.json`. Секретные значения в рамках исследования не читались/не выводились. Сохранённый корпус:9PDF/64страницы/1445кандидатов/11verified на4страницах; это частичное ручное покрытие, а не утверждение всего OCR.

## Кейс и расширение цели

В DOMAINS_AND_OWNERS D04 для первого MVP прямо предложен один доступный внешний источник. Вебинар34–39 не задаёт обязательного списка/числа; законные бесплатные RSS/HTML/API допустимы, резервные источники полезны. Новая цель по замечанию команды — несколько независимых реальных издателей и полный пользовательский путь. Рекомендуемое число источников в новом spec — инженерное решение, не вымышленный норматив кейса.

Несколько страниц/RSS ЦБ не считаются несколькими издателями. Request-time official enrichment и финансовые показатели отдельно от persisted новостного конвейера. HTTP200 пустого RSS не доказывает работающую загрузку. Свободный доступ к странице и разрешение robots не заменяют условия использования.

Параллельные доказательства: [01-branches-integration.md](01-branches-integration.md), [02-open-sources.md](02-open-sources.md), [03-acceptance-gaps.md](03-acceptance-gaps.md).

## Работа по чатам

Отдельный пользовательский чат `Общий Docker Compose и DevOps business-insights`, id `01a174e1-4e8b-7be1-b329-dbe6ac570f85`, уже выполняет поручение по общему запуску на своей feature/full-stack-compose. Он владеет Compose/инфраструктурой и минимальными runtime adapters. Его работа не считается завершённой до отдельного отчёта. Этот чат владеет исследованием/планом D03/D04; реализационные чаты и правила записи задаются в плане после согласования.

Three-way симуляция подтверждает, что обычный merge ML сохраняет Backend/import/materials и конфликтует только в .gitignore; merge UI — только в frontend/README. Отсутствие этих директорий в endpoint snapshot ML не означает их удаления при merge. Предпочтителен полный merge взаимосвязанных ML-изменений, а не повторное ручное копирование.

Ruflo/ToolSearch MCP не обнаружены. Использованы штатные Codex tools, отдельный чат и три read-only исследовательских субагента. Продуктовый код, общие ветки, raw datasets и live LLM этим исследованием не менялись.

## Обновление DevOps в конце исследования

Отдельный чат завершил реализацию: draftPR7 https://github.com/AyKezBan/business-insights/pull/7, headb177c7d. Ветка включает UI7c59dd0; отдельный повторный merge того жеUI не нужен. Созданы общий Compose/stack wrapper, nginx auth/proxy и frontend/web/backend-bindings.js, import/material jobs. По infra/VERIFICATION.md реальные образы собраны, два smoke с disposable PostgreSQL и синтетическим ML выполнены, повторный импорт/рестарт проверены; платный live LLM этим не подтверждён. План финализации должен расширять эти bindings/proxy, не делать ещё один адаптер.
