# Проверка проекта плана — 26.10.2026

## Что проверено независимо

Три research субагента независимо разобрали ветки/интеграцию, реальные открытые источники и границы приёмки. Четвёртый fresh-context builder получил написанный design, без задачи писать код. Он нашёл два важных вопроса:

1. Одна event version может входить в несколько corpus revisions. CAS только по event hash не продвигает новый банковский корпус и не защищает от позднего старого sync.
2. Publication cutoff не определяет историческую наблюдаемую версию исправленной статьи. Текущий корпус с новой правкой и прежней датой нельзя молча назвать point-in-time replay.

Оба вопроса решены в spec и T07/T09: отдельный CatalogToken(store_id,generation,revision), монотонный CAS и bank-only update regression; явно ограниченный publication cutoff MVP с limited/warning для неподтверждённой исторической реконструкции. Старый run привязан к сохранённой revision. Истинный bitemporal replay — отдельное расширение. Это решения автора проекта плана для согласования, не ранее полученное одобрение пользователя.

## Самопроверка плана по writing-plans

| Область spec | Задачи | Проверяемая граница |
| --- | --- | --- |
| Свежие teammates и общий стек | T01 | Полный ML merge, готовый DevOps PR7 с UI, новый baseline вместо stale worktree |
| Metadata/precision/legacy/registry/transport | T02 | v1 hash до новых defaults, v2 не затирает старые artifacts, три fixed publisher policies |
| Новые источники | T03/T04 | ФНС HTML непустой, EIA permitted HTML; даты/units/география/rights явны; не три страницы ЦБ |
| Failure isolation и версия | T05 | Сохранность records, sync0/1/2/3, отдельный run report, повторный load под lock |
| Банк/доказательства/gold | T06 |64dispositions, расширенное реальное verified покрытие, независимый gold и близкие отрицательные случаи |
| Runtime/PDF/QA/history | T07 | In-flight pin, old QA/PDF, catalog token, публикационный cutoff явно ограничен |
| Java сохранение и ошибки | T08 | Round-trip полей/arrays/outcome/profile, schemas/compatibility warnings актуальны |
| Новый event/update/очередь | T09 | Legacy409 остаётся; trusted CAS + frozen event/profile/corpus before queue; no auto LLM fan-out |
| Semantic defects | T10 | Спаны и один существующий verifier/budget; actor/negation/time/unit/translation cases не скрываются numeric membership |
| Настоящий browser path | T11 | Расширение bindings/proxy PR7, не второй transport; document/page/source/QA/trace/persistence |
| Полнота приёмки | T12 |9новых пар+9QA, бюджет до вызовов, независимый запуск; scripted green не A10 |

Проверены типы и интерфейсы: SourceSpec/FetchBatch/DiscoveredArticle/RawNews topics задаются T02; FNS/EIA используют их, а не собственные несовместимые DTO. SyncResult/CorpusBundle/CatalogToken имеют названные производители и consumers. Общие Java пути заданы aliasJ с единственным корнем. Shared models/registry/CLI/schema/state не распределяются конкурентно. Git index каждого implementer worktree отдельный.

Пять Review Focus каждого имеют owning tasks и регрессии. Plan имеет12задач, gates, зависимости, meaningful RED/GREEN и handoff; setup/docs не требуют искусственных тестов. Внешние source HTTP пробы, synth/offline тесты и будущая real acceptance различаются. Опциональные publishers/архивы/embeddings не включены в обещанный P0 результат.

## Существенные собственные рекомендации

- Тройка CBR/FNS/EIA выбрана по текущей доступности и опубликованным условиям, не по вымышленному правилу кейса. EIA не российский источник; география и перевод маркируются.
- Цели30verified/6документов/6тем, extension12/10hits, near-negatives6 и live9пар — предложенные release gates. Фактическая нехватка usable банковского текста фиксируется как gap, не заполняется выдуманными решениями.
- Budget100руб на финальную сессию — предлагаемый ограничитель, а не проверенный остаток ключа. Реальный provider/preflight выполняется только на стадии исполнения.
- Runtime hot reload нужен для управляемого регулярного потока, но не меняет in-flight/old evidence. Его отсутствие нельзя скрыть запуском scheduler.
- Дата плана соответствует client context26.10; текущие тарифы и остаток ключа не проверялись. Старые рабочие предложения20–22октября не задают новый дедлайн.

## Что не выполнялось этим планированием

Продуктовый код D03/D04 не менялся, новые источники не названы реализованными от одних HTTP-проб. Не запускались paid LLM, не читались значения ключей, не импортировались повторно raw CSV и не изменялись старые корпуса. DevOps отдельно выполнил авторизованное поручение и создал PR7; его synthetic smoke не является live смысловой приёмкой.
