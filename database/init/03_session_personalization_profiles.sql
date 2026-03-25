CREATE TABLE IF NOT EXISTS session_personalization_profiles (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT NOT NULL REFERENCES users(id),
    device_id TEXT REFERENCES devices(id),
    session_id BIGINT REFERENCES sessions(id),
    source_discrepancy_analysis_id BIGINT REFERENCES session_discrepancy_analyses(id),
    model_name TEXT NOT NULL,
    profile_confidence DOUBLE PRECISION,
    profile_payload JSONB NOT NULL,
    primary_sensitivity TEXT,
    bias_direction TEXT,
    recovery_speed TEXT,
    task_type TEXT,
    time_of_day TEXT
);

CREATE INDEX IF NOT EXISTS session_personalization_profiles_user_created_idx
    ON session_personalization_profiles (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS session_personalization_profiles_session_idx
    ON session_personalization_profiles (session_id);
