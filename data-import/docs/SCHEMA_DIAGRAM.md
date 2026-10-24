# Диаграмма БД

Источник: [02_tables.sql](../sql/02_tables.sql). Схема `business_insights`, 6 таблиц.

`PK` — первичный ключ, `FK` — внешний ключ, `UK` — уникальность. `NOT NULL` указан в комментариях полей; первичные ключи также обязательны. `TEXT[]` — массив строк.

```mermaid
erDiagram
    dataset_metadata {
        BOOLEAN singleton PK "DEFAULT TRUE; CHECK TRUE"
        TEXT schema_version "NOT NULL"
        TEXT dataset_sha256 "NOT NULL"
        TIMESTAMPTZ loaded_at "NOT NULL; DEFAULT now()"
        JSONB source_manifest "NOT NULL"
        DATE payment_period_start
        DATE payment_period_end
        TEXT payment_period_description "NOT NULL"
        INTEGER revenue_year "NOT NULL; DEFAULT 2025"
        TEXT currency "NOT NULL; DEFAULT RUB"
    }
    company_ref {
        TEXT synthetic_u PK
        TEXT profile_status "NOT NULL; available / ambiguous / external"
    }
    company_profile {
        TEXT synthetic_u PK,FK
        BIGINT source_row UK "NOT NULL; CSV row"
        TEXT org_type
        TEXT okfs_name
        TEXT company_age_years_gradation
        TEXT eco_mark_label
        TEXT mainokved_code
        TEXT mainokved_name
        BOOLEAN negativemark
        TEXT amount_workers_fns_gradation
        TEXT amount_workers_spark_gradation
        TEXT cl_type "CLIENT / PROSPECT / CLOSE_CLIENT"
        BOOLEAN cl_product_rko_mark
        BOOLEAN cl_product_gar_mark
        BOOLEAN cl_product_loa_mark
        BOOLEAN cl_product_lea_mark
        BOOLEAN cl_product_dep_mark
        BOOLEAN cl_product_zpp_mark
        BOOLEAN fts_flag
        TEXT fts_country_name
        BOOLEAN factoring_mark
        NUMERIC revenue_company ">= 0"
        TEXT group_id
        TEXT city
        TEXT region
        TEXT revenue_status "NOT NULL; observed / placeholder / missing / invalid"
        TEXT[] fts_countries
        TEXT[] quality_issues "NOT NULL"
    }
    person_ref {
        TEXT synthetic_f PK
    }
    person_company_link {
        BIGINT source_row PK "CSV row"
        TEXT synthetic_f FK "NOT NULL"
        TEXT synthetic_u FK "NOT NULL"
        TEXT city_name
        TEXT rc_name
    }
    payment_snapshot {
        BIGINT row_id PK "CSV row"
        TEXT payer FK
        TEXT beneficiary FK
        NUMERIC pay_amt ">= 0"
        BIGINT pay_cnt ">= 0"
        BOOLEAN ml_eligible "NOT NULL"
        TEXT[] quality_issues "NOT NULL"
    }

    company_ref ||--o| company_profile : synthetic_u
    person_ref ||--o{ person_company_link : synthetic_f
    company_ref ||--o{ person_company_link : synthetic_u
    company_ref |o--o{ payment_snapshot : payer
    company_ref |o--o{ payment_snapshot : beneficiary
```

`dataset_metadata` хранит одну запись о текущем наборе и не связан с другими таблицами внешними ключами. Исходные записи остаются в CSV; `source_row` и `row_id` обозначают номер записи исходного файла без заголовка и не являются внешними ключами.

В `company_profile` выручка заполнена тогда и только тогда, когда `revenue_status = 'observed'`. Для платежей с `ml_eligible = TRUE` обязательны обе стороны, сумма и счётчик. Пара `payer`–`beneficiary` не уникальна. История наборов не реализована.

Дополнительные индексы: `company_profile(group_id)`, `person_company_link(synthetic_u)`, `person_company_link(synthetic_f)`, `payment_snapshot(payer)`, `payment_snapshot(beneficiary)`.
