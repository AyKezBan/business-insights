CREATE INDEX IF NOT EXISTS company_profile_group_idx ON business_insights.company_profile(group_id);
CREATE INDEX IF NOT EXISTS person_link_company_idx ON business_insights.person_company_link(synthetic_u);
CREATE INDEX IF NOT EXISTS person_link_person_idx ON business_insights.person_company_link(synthetic_f);
CREATE INDEX IF NOT EXISTS payment_payer_idx ON business_insights.payment_snapshot(payer);
CREATE INDEX IF NOT EXISTS payment_beneficiary_idx ON business_insights.payment_snapshot(beneficiary);
