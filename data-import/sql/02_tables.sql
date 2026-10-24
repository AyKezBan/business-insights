CREATE TABLE IF NOT EXISTS business_insights.dataset_metadata (
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
CREATE TABLE IF NOT EXISTS business_insights.company_ref (
    synthetic_u TEXT PRIMARY KEY,
    profile_status TEXT NOT NULL CHECK (profile_status IN ('available', 'ambiguous', 'external'))
);
CREATE TABLE IF NOT EXISTS business_insights.company_profile (
    synthetic_u TEXT PRIMARY KEY REFERENCES business_insights.company_ref,
    source_row BIGINT UNIQUE NOT NULL,
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
CREATE TABLE IF NOT EXISTS business_insights.person_ref (
    synthetic_f TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS business_insights.person_company_link (
    source_row BIGINT PRIMARY KEY,
    synthetic_f TEXT NOT NULL REFERENCES business_insights.person_ref,
    synthetic_u TEXT NOT NULL REFERENCES business_insights.company_ref,
    city_name TEXT,
    rc_name TEXT
);
CREATE TABLE IF NOT EXISTS business_insights.payment_snapshot (
    row_id BIGINT PRIMARY KEY,
    payer TEXT REFERENCES business_insights.company_ref,
    beneficiary TEXT REFERENCES business_insights.company_ref,
    pay_amt NUMERIC CHECK (pay_amt >= 0),
    pay_cnt BIGINT CHECK (pay_cnt >= 0),
    ml_eligible BOOLEAN NOT NULL,
    quality_issues TEXT[] NOT NULL,
    CHECK (NOT ml_eligible OR (payer IS NOT NULL AND beneficiary IS NOT NULL
                              AND pay_amt IS NOT NULL AND pay_cnt IS NOT NULL))
);
