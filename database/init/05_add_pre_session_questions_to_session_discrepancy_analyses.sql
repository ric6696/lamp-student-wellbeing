ALTER TABLE session_discrepancy_analyses
    ADD COLUMN IF NOT EXISTS pre_session_questions JSONB;