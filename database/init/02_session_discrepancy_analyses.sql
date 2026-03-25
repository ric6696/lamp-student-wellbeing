CREATE TABLE IF NOT EXISTS session_discrepancy_analyses (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT NOT NULL REFERENCES users(id),
    device_id TEXT REFERENCES devices(id),
    session_id BIGINT REFERENCES sessions(id),
    model_name TEXT NOT NULL,
    model_score DOUBLE PRECISION NOT NULL,
    user_score DOUBLE PRECISION NOT NULL,
    score_gap DOUBLE PRECISION NOT NULL,
    discrepancy_reasoning JSONB NOT NULL,
    raw_llm_response TEXT,
    prompt_used TEXT
);

CREATE INDEX IF NOT EXISTS session_discrepancy_analyses_user_created_idx
    ON session_discrepancy_analyses (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS session_discrepancy_analyses_session_idx
    ON session_discrepancy_analyses (session_id);
