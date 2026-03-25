CREATE TABLE IF NOT EXISTS user_personalization_profiles (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_discrepancy_analysis_id BIGINT REFERENCES session_discrepancy_analyses(id),
    profile_confidence DOUBLE PRECISION,
    profile_payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS user_personalization_profiles_updated_idx
    ON user_personalization_profiles (updated_at DESC);
