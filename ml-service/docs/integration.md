# Интеграция ML с импортом данных и интерфейсом

Сверено с ветками `feature/data-import` (схема БД, `DATA_RULES.md`) и `feature/d09-d10-clarity` (`API_CONTRACT.md`, версия 2.0). Java Backend уже реализован и объединён в develop (PR #5, 5572d5a), а общий runtime и UI bindings находятся в DevOps PR #7. Ниже описан целевой обмен; сохранность новых metadata, version context и outcome отдельно проверяется задачами T08/T09 согласованной финализации.

## Импорт данных -> запрос ML (`POST /v1/insights`)

Backend собирает `profile` из `company_profile`, `payment_snapshot` и `dataset_metadata`. Неизвестное остаётся `null`, нулём или `false` не заменяется.

| Источник (БД) | Поле профиля ML | Примечание |
|---|---|---|
| `synthetic_u` | `company_id` | |
| `group_id` | `group_id` | состав группы импорт не восстанавливает |
| `mainokved_code`, `mainokved_name` | `okved_code`, `okved_name` | |
| `revenue_company` | `revenue_rub` | `Decimal`: число или строка; при `revenue_status` не `observed` значение пустое |
| `revenue_status` | `revenue_status` | `observed` принимается и приводится к `known`; `placeholder`, `missing`, `invalid` без изменений |
| `dataset_metadata.revenue_year` | `revenue_year` | |
| `cl_product_rko_mark`, `_gar_`, `_loa_`, `_lea_`, `_dep_`, `_zpp_`, `factoring_mark` | `products.settlement`, `guarantees`, `loans`, `leasing`, `deposits`, `payroll`, `factoring` | `NULL` остаётся `null` |
| `fts_flag`, `fts_countries` | `foreign_trade`, `foreign_trade_countries` | `false` не означает отсутствие торговли; направление (импорт или экспорт) в данных неизвестно, ML его не выводит |
| `cl_type` | `client_type` | `CLIENT`, `PROSPECT`, `CLOSE_CLIENT` |
| `okfs_name`, `company_age_years_gradation`, `eco_mark_label` | `ownership`, `company_age`, `business_stage` | |
| `amount_workers_fns_gradation` / `_spark_` | `employees` | два источника остаются раздельными; конфликт из `quality_issues` передаётся в `profile_quality_notes` |
| `city`, `region` | `city`, `region` | регистрация юрлица, не место обслуживания |
| `quality_issues` | `profile_quality_notes` | |
| `payment_period_description` | `payment_period` | известно только «с 1 января» |
| `payment_snapshot` с `ml_eligible=true` | `counterparties`, `self_transfer_share` | агрегирует Backend: направление `incoming/outgoing`, `amount_rub`, `share_of_flow`; переводы себе (`payer = beneficiary`) считаются в `self_transfer_share`; как контрагент и как признак для модели ML их не использует (долю он показывает в примечании к контрагентам) |

Не используются: `negativemark` (это не флаг риска), связи физлиц (должность и роль пользователя из них не следуют), `org_type`.

Дополнительно в запросе: `role` (`cfo`, `ceo`, ...), `event`, необязательные `request_id` (повтор без новых платных вызовов; другое содержимое с тем же id - `409`), `parent_run_id`, `as_of` (срез по времени; событие позже `as_of` - `422`).

## ML -> интерфейс (контракт 2.0)

| Что нужно интерфейсу | Эндпоинт ML |
|---|---|
| `GET /api/insight` (карточка с блоками) | `GET /v1/insights/{insight_id}/card?view=overview\|consequences\|risks\|recommendations` |
| `POST /api/clarifications` | `POST /v1/insights/{insight_id}/clarification` с телом `{question, request_id, view}` |
| `GET /api/feed`, `/api/bootstrap`, авторизация, доступ к компаниям | не ML: Backend (отбор и ранжирование D01, сессия) |

Соответствие карточки: факты события -> блок `fact`; позиция банка -> `bank`; выводы модели с признаками компании -> `ai`; выводы модели без признаков компании -> `open`. Темы: `consequences` (почему важно, последствия, возможности), `risks`, `recommendations` (действия). Метки `certainty` те же, что знает интерфейс (`fact`, `inference`, `hypothesis`, `assessment`, `recommendation`, `unavailable`). Позиция банка попадает только в `bank`, при отсутствии материала каждый раздел банка сообщает об этом с пустым `source_ids`. В тематических видах блока `fact` нет. Источники: `id`, `title`, `date`, `page` (плюс `kind`, `url`).

Статусы карточки: `status` (`complete`, `limited`, `failed`), `outcome` (`full`, `limited`, `refused`, `none`), `error_code`. Уточнение возвращает `status` `answered` или `no_answer`, `request_id` из запроса и `answer_id`.

Ошибки ML: `404` (нет инсайта, он хранится на диске и переживает перезапуск), `409` (`request_id` с другим содержимым), `422` (неверный `view`, `as_of` раньше события, неверный запрос). Формат `{"error": {"message": ...}}` из контракта интерфейса Backend строит сам; ML отдаёт стандартный `detail` FastAPI.

## Расхождения, которые остаются на стороне Backend

- Идентификатор карточки интерфейса (`retail:rate:v2`) и ML (`<company>:<event>:<run>`) разные: Backend хранит соответствие.
- Роли интерфейса: только `cfo` и `ceo`, ML понимает ещё `owner`, `chief_accountant`, `treasurer`, `other_c_level`, `other`.
- Оценки и скрытие карточек интерфейс пока хранит локально; серверное хранение, `401/403`, `429` и очередь для долгих запусков (в контракте названа job API) - Backend.
- Контракт интерфейса требует таймаут 15 с на клиенте, а анализ одной пары в ML может идти дольше (лимит `AGENT_MAX_SECONDS`, по умолчанию 120 с). Нужен асинхронный запуск или предварительный расчёт: решение за Backend.

## Приёмка ответа ML в backend-api

`HttpMlClient` проверяет ответ по закреплённым схемам и отклоняет **весь** ответ как недопустимый, если:
- модель сослалась на признак вне перечня `okved_code`, `revenue_rub`, `group_revenue_rub`, `foreign_trade`, `group_member_okveds`, `region`, `products.*` (только если значение `true`), `user_provided.*`, `counterparties[i]` (индекс должен быть меньше длины отправленного списка), либо признак пустой или `false`;
- в ответе пустые или повторяющиеся `source_id`, ссылка на источник отсутствует в `sources`, `fact` или `assessment` без источника, позиция банка ссылается не на банковский источник;
- `company_id`, `event_id` или `role` в ответе не совпадают с запросом.

ML этому соответствует: перечень признаков, которые видит модель, совпадает с перечнем backend (тест `tests/test_backend_contract.py`), индексы контрагентов исходные, новые поля ответа (`outcome`, `error_code`, `claim_id`, `cache_hit`, `version`, `request_id`, `parent_run_id`) backend игнорирует (`ignoreUnknown`), а схемы ответа не запрещают лишние поля. Лишние поля в запросе backend не отправляет (`request_id`, `as_of` необязательны); для повторов без новых платных вызовов ему стоит передавать `request_id`.
