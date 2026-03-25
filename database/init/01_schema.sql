-- Extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Drop old objects to allow idempotent rebuilds during early development
DROP MATERIALIZED VIEW IF EXISTS audio_exposure_10m_stats CASCADE;
DROP TABLE IF EXISTS audio_events CASCADE;
DROP TABLE IF EXISTS motion_events CASCADE;
DROP TABLE IF EXISTS events CASCADE;
DROP TABLE IF EXISTS gps CASCADE;
DROP TABLE IF EXISTS vitals CASCADE;
DROP TABLE IF EXISTS session_discrepancy_analyses CASCADE;
DROP TABLE IF EXISTS session_personalization_profiles CASCADE;
DROP TABLE IF EXISTS sessions CASCADE;
DROP TABLE IF EXISTS devices CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS metric_catalog CASCADE;

-- 0. Metric catalog (authoritative codes)
CREATE TABLE metric_catalog (
    code SMALLINT PRIMARY KEY,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    description TEXT,
    enabled BOOLEAN DEFAULT TRUE
);

INSERT INTO metric_catalog (code, name, unit, description) VALUES
    (1, 'heart_rate_bpm', 'count/min', 'Heart rate in beats per minute'),
    (2, 'heart_rate_variability_sdnn_ms', 'ms', 'Heart rate variability SDNN in milliseconds'),
    (10, 'environmental_noise_db', 'dBA', 'Ambient or environmental noise level'),
    (20, 'steps', 'count', 'Step count increment'),
    (21, 'distance_m', 'meter', 'Distance walked/running');

-- 1. Core identities
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    external_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    platform TEXT,
    model_name TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_sync TIMESTAMPTZ
);

CREATE TABLE sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    session_key TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    label TEXT
);
CREATE UNIQUE INDEX sessions_user_session_key_idx ON sessions (user_id, session_key) WHERE session_key IS NOT NULL;

-- 1b. Study-session discrepancy analysis output
CREATE TABLE session_discrepancy_analyses (
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
CREATE INDEX session_discrepancy_analyses_user_created_idx ON session_discrepancy_analyses (user_id, created_at DESC);
CREATE INDEX session_discrepancy_analyses_session_idx ON session_discrepancy_analyses (session_id);

-- 1c. Session-level personalization extraction output
CREATE TABLE session_personalization_profiles (
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
CREATE INDEX session_personalization_profiles_user_created_idx
    ON session_personalization_profiles (user_id, created_at DESC);
CREATE INDEX session_personalization_profiles_session_idx
    ON session_personalization_profiles (session_id);

-- 2. Vitals (numeric time series)
CREATE TABLE vitals (
    time TIMESTAMPTZ NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    session_id BIGINT REFERENCES sessions(id),
    metric_code SMALLINT NOT NULL REFERENCES metric_catalog(code),
    value DOUBLE PRECISION NOT NULL,
    metadata JSONB,
    PRIMARY KEY (time, device_id, metric_code)
);
SELECT create_hypertable('vitals', 'time');
CREATE INDEX ON vitals (user_id, time DESC);
CREATE INDEX ON vitals (user_id, metric_code, time DESC);
CREATE INDEX ON vitals (session_id, time);

-- 3. GPS (with optional coarse coords and place hints)
CREATE TABLE gps (
    time TIMESTAMPTZ NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    session_id BIGINT REFERENCES sessions(id),
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    acc DOUBLE PRECISION,
    coarse_lat DOUBLE PRECISION,
    coarse_lon DOUBLE PRECISION,
    metadata JSONB,
    PRIMARY KEY (time, device_id)
);
SELECT create_hypertable('gps', 'time');
CREATE INDEX ON gps (user_id, time DESC);
CREATE INDEX ON gps (session_id, time);

-- 4. Motion context events
CREATE TABLE motion_events (
    time TIMESTAMPTZ NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    session_id BIGINT REFERENCES sessions(id),
    context TEXT NOT NULL,
    metadata JSONB,
    PRIMARY KEY (time, device_id, context)
);
SELECT create_hypertable('motion_events', 'time');
CREATE INDEX ON motion_events (user_id, time DESC);
CREATE INDEX ON motion_events (session_id, time);

-- 5. Audio context events
CREATE TABLE audio_events (
    time TIMESTAMPTZ NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    session_id BIGINT REFERENCES sessions(id),
    label TEXT NOT NULL,
    db DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    ai_label TEXT,
    ai_confidence DOUBLE PRECISION,
    metadata JSONB,
    PRIMARY KEY (time, device_id, label)
);
SELECT create_hypertable('audio_events', 'time');
CREATE INDEX ON audio_events (user_id, time DESC);
CREATE INDEX ON audio_events (session_id, time);

-- 6. Generic events (session markers, sleep stages, etc.)
CREATE TABLE events (
    time TIMESTAMPTZ NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    session_id BIGINT REFERENCES sessions(id),
    label TEXT NOT NULL,
    val_text TEXT,
    metadata JSONB,
    PRIMARY KEY (time, device_id, label)
);
SELECT create_hypertable('events', 'time');
CREATE INDEX ON events (user_id, time DESC);
CREATE INDEX ON events (session_id, time);

-- 7. Session-aligned 10-minute audio exposure stats (relative to session start/end)
-- Example: session 12:06 -> 13:01 produces buckets 12:06-12:16, 12:16-12:26, ... 12:56-13:01.
CREATE OR REPLACE FUNCTION get_session_audio_exposure_10m_stats(p_session_id BIGINT)
RETURNS TABLE (
    session_id BIGINT,
    bucket_index INTEGER,
    bucket_start TIMESTAMPTZ,
    bucket_end TIMESTAMPTZ,
    sample_count BIGINT,
    mean_audio_exposure DOUBLE PRECISION,
    stddev_audio_exposure DOUBLE PRECISION
)
LANGUAGE SQL
STABLE
AS $$
WITH session_bounds AS (
    SELECT
        s.id AS session_id,
        s.user_id,
        s.device_id,
        s.started_at,
        COALESCE(s.ended_at, now()) AS ended_at,
        GREATEST(
            0,
            CEIL(EXTRACT(EPOCH FROM (COALESCE(s.ended_at, now()) - s.started_at)) / 600.0)::INT
        ) AS bucket_count
    FROM sessions s
    WHERE s.id = p_session_id
),
bucket_ranges AS (
    SELECT
        sb.session_id,
        gs AS bucket_index,
        sb.started_at + (gs * INTERVAL '10 minutes') AS bucket_start,
        LEAST(sb.started_at + ((gs + 1) * INTERVAL '10 minutes'), sb.ended_at) AS bucket_end,
        sb.user_id,
        sb.device_id
    FROM session_bounds sb
    CROSS JOIN LATERAL generate_series(0, GREATEST(sb.bucket_count - 1, 0)) AS gs
)
SELECT
    br.session_id,
    br.bucket_index,
    br.bucket_start,
    br.bucket_end,
    COUNT(v.value)::BIGINT AS sample_count,
    AVG(v.value)::DOUBLE PRECISION AS mean_audio_exposure,
    STDDEV_SAMP(v.value)::DOUBLE PRECISION AS stddev_audio_exposure
FROM bucket_ranges br
LEFT JOIN vitals v
    ON v.user_id = br.user_id
   AND v.device_id = br.device_id
   AND v.metric_code = 10
   AND v.time >= br.bucket_start
   AND v.time < br.bucket_end
GROUP BY br.session_id, br.bucket_index, br.bucket_start, br.bucket_end
ORDER BY br.bucket_start;
$$;
