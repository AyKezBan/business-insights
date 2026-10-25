-- Imported business_insights tables are owned exclusively by data-import.
CREATE TABLE backend_api.event (
    event_id text PRIMARY KEY,
    content_hash text NOT NULL,
    payload jsonb NOT NULL,
    published_at timestamptz,
    received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE backend_api.operation (
    id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    company_id text NOT NULL,
    role text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('ANALYSIS','QUESTION')),
    parent_id uuid REFERENCES backend_api.operation(id),
    event_id text REFERENCES backend_api.event(event_id),
    request_key text,
    request_hash text NOT NULL,
    status text NOT NULL CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','LIMITED','NO_RESULT','INSUFFICIENT_DATA','FAILED')),
    payload jsonb NOT NULL,
    profile_snapshot jsonb,
    result jsonb,
    trace jsonb,
    trace_state text NOT NULL DEFAULT 'PENDING',
    warnings jsonb NOT NULL DEFAULT '[]',
    error_code text,
    error_message text,
    retryable boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id, kind, request_key)
);
CREATE INDEX operation_feed ON backend_api.operation(owner_id, company_id, role, created_at DESC) WHERE kind='ANALYSIS';
CREATE INDEX operation_parent ON backend_api.operation(parent_id);
CREATE TABLE backend_api.insight_state (
    operation_id uuid NOT NULL REFERENCES backend_api.operation(id),
    owner_id text NOT NULL,
    hidden boolean NOT NULL DEFAULT false,
    opened_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(operation_id,owner_id)
);
CREATE TABLE backend_api.feedback (
    operation_id uuid NOT NULL REFERENCES backend_api.operation(id),
    owner_id text NOT NULL,
    rating text NOT NULL CHECK (rating IN ('USEFUL','IRRELEVANT','UNSUPPORTED','OTHER')),
    comment text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(operation_id,owner_id)
);
CREATE TABLE backend_api.action_intent (
    id uuid PRIMARY KEY,
    operation_id uuid NOT NULL REFERENCES backend_api.operation(id),
    owner_id text NOT NULL,
    action text NOT NULL CHECK (action IN ('CONTACT_MANAGER','REQUEST_CALLBACK','REQUEST_ANALYSIS')),
    comment text,
    status text NOT NULL DEFAULT 'RECORDED' CHECK (status='RECORDED'),
    created_at timestamptz NOT NULL DEFAULT now()
);
