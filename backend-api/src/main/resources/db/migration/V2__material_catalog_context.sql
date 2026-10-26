-- Trusted material publication state; imported business_insights tables stay untouched.
CREATE TABLE backend_api.material_catalog_head (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    store_id text,
    generation bigint,
    revision text,
    CHECK ((store_id IS NULL AND generation IS NULL AND revision IS NULL)
        OR (store_id IS NOT NULL AND generation >= 1 AND revision ~ '^[a-f0-9]{64}$'))
);
INSERT INTO backend_api.material_catalog_head(singleton) VALUES (true);
ALTER TABLE backend_api.event
    ADD COLUMN catalog_store_id text,
    ADD COLUMN catalog_generation bigint,
    ADD COLUMN corpus_revision text,
    ADD COLUMN event_content_sha256 text,
    ADD COLUMN ml_payload jsonb;
CREATE INDEX event_catalog_generation ON backend_api.event(catalog_store_id, catalog_generation);
