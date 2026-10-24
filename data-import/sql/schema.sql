-- PostgreSQL 17+. Dedicated schema; this file never drops existing objects.
CREATE SCHEMA IF NOT EXISTS bi_data;

CREATE TABLE IF NOT EXISTS bi_data.dataset_metadata (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    schema_version TEXT NOT NULL,
    dataset_sha256 TEXT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_manifest JSONB NOT NULL,
    payment_period_start DATE,
    payment_period_end DATE,
    payment_period_description TEXT NOT NULL,
    revenue_year INTEGER NOT NULL DEFAULT 2025,
    currency TEXT NOT NULL DEFAULT 'RUB'
);
CREATE TABLE IF NOT EXISTS bi_data.raw_company (
    source_row BIGINT PRIMARY KEY, payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS bi_data.raw_link (
    source_row BIGINT PRIMARY KEY, payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS bi_data.raw_payment (
    source_row BIGINT PRIMARY KEY, payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS bi_data.company_ref (
    synthetic_u TEXT PRIMARY KEY,
    profile_status TEXT NOT NULL CHECK (profile_status IN ('available', 'ambiguous', 'external'))
);
CREATE TABLE IF NOT EXISTS bi_data.company_profile (
    synthetic_u TEXT PRIMARY KEY REFERENCES bi_data.company_ref,
    source_row BIGINT UNIQUE NOT NULL REFERENCES bi_data.raw_company,
    org_type TEXT,
    okfs_name TEXT,
    company_age_years_gradation TEXT,
    eco_mark_label TEXT,
    mainokved_code TEXT,
    mainokved_name TEXT,
    negativemark BOOLEAN,
    amount_workers_fns_gradation TEXT,
    amount_workers_spark_gradation TEXT,
    cl_type TEXT CHECK (cl_type IN ('CLIENT','PROSPECT','CLOSE_CLIENT')),
    cl_product_rko_mark BOOLEAN,
    cl_product_gar_mark BOOLEAN,
    cl_product_loa_mark BOOLEAN,
    cl_product_lea_mark BOOLEAN,
    cl_product_dep_mark BOOLEAN,
    cl_product_zpp_mark BOOLEAN,
    fts_flag BOOLEAN,
    fts_country_name TEXT,
    factoring_mark BOOLEAN,
    revenue_company NUMERIC CHECK (revenue_company >= 0),
    group_id TEXT,
    city TEXT,
    region TEXT,
    revenue_status TEXT NOT NULL CHECK (revenue_status IN ('observed','placeholder','missing','invalid')),
    fts_countries TEXT[],
    quality_issues TEXT[] NOT NULL,
    CHECK ((revenue_status = 'observed') = (revenue_company IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS bi_data.person_ref (
    synthetic_f TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS bi_data.person_company_link (
    source_row BIGINT PRIMARY KEY REFERENCES bi_data.raw_link,
    synthetic_f TEXT NOT NULL REFERENCES bi_data.person_ref,
    synthetic_u TEXT NOT NULL REFERENCES bi_data.company_ref,
    city_name TEXT,
    rc_name TEXT
);
CREATE TABLE IF NOT EXISTS bi_data.payment_snapshot (
    row_id BIGINT PRIMARY KEY REFERENCES bi_data.raw_payment(source_row),
    payer TEXT REFERENCES bi_data.company_ref,
    beneficiary TEXT REFERENCES bi_data.company_ref,
    pay_amt NUMERIC CHECK (pay_amt >= 0),
    pay_cnt BIGINT CHECK (pay_cnt >= 0),
    ml_eligible BOOLEAN NOT NULL,
    quality_issues TEXT[] NOT NULL,
    CHECK (NOT ml_eligible OR (payer IS NOT NULL AND beneficiary IS NOT NULL
                              AND pay_amt IS NOT NULL AND pay_cnt IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS company_profile_group_idx ON bi_data.company_profile(group_id);
CREATE INDEX IF NOT EXISTS person_link_company_idx ON bi_data.person_company_link(synthetic_u);
CREATE INDEX IF NOT EXISTS person_link_person_idx ON bi_data.person_company_link(synthetic_f);
CREATE INDEX IF NOT EXISTS payment_payer_idx ON bi_data.payment_snapshot(payer);
CREATE INDEX IF NOT EXISTS payment_beneficiary_idx ON bi_data.payment_snapshot(beneficiary);
COMMENT ON COLUMN bi_data.company_profile.negativemark IS 'Known key-owner information, not a negative-risk label';
COMMENT ON COLUMN bi_data.company_profile.revenue_company IS 'RUB 2025, placeholder 1000 -> NULL; company/group scope unresolved';
COMMENT ON COLUMN bi_data.person_company_link.city_name IS 'Webinar: service city of legal entity; preserve in link pending clarification';
COMMENT ON COLUMN bi_data.payment_snapshot.row_id IS 'Logical data-row number in original payment CSV, header excluded';
COMMENT ON TABLE bi_data.payment_snapshot IS 'One current dataset; no snapshot history, no unique payer-beneficiary constraint';
