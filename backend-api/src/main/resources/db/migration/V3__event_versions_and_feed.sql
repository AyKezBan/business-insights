ALTER TABLE backend_api.event ADD COLUMN current_version bigint NOT NULL DEFAULT 1 CHECK (current_version > 0);

CREATE TABLE backend_api.event_revision (
    event_id text NOT NULL REFERENCES backend_api.event(event_id),
    version bigint NOT NULL CHECK (version > 0),
    content_hash text NOT NULL,
    payload jsonb NOT NULL,
    published_at timestamptz,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(event_id, version)
);
INSERT INTO backend_api.event_revision(event_id, version, content_hash, payload, published_at, received_at)
SELECT event_id, current_version, content_hash, payload, published_at, received_at FROM backend_api.event;

ALTER TABLE backend_api.operation
    ADD COLUMN event_version bigint NOT NULL DEFAULT 1,
    ADD COLUMN stale boolean NOT NULL DEFAULT false,
    ADD COLUMN expires_at timestamptz,
    ADD COLUMN relevance_score integer NOT NULL DEFAULT 0 CHECK (relevance_score BETWEEN 0 AND 100),
    ADD COLUMN relevance_reasons jsonb NOT NULL DEFAULT '[]',
    ADD CONSTRAINT operation_event_revision FOREIGN KEY(event_id, event_version)
        REFERENCES backend_api.event_revision(event_id, version);

-- Existing explicit analyses retain their content and qualify until the same default expiry.
UPDATE backend_api.operation o SET expires_at=COALESCE(e.published_at,e.received_at)+interval '30 days'
FROM backend_api.event e WHERE o.event_id=e.event_id;
CREATE INDEX operation_event_version ON backend_api.operation(event_id,event_version);
CREATE INDEX event_revision_hash ON backend_api.event_revision(event_id,content_hash);
