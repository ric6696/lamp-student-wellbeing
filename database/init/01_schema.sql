-- EXTENSIONS
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 1. DEVICE METADATA
CREATE TABLE devices (
    device_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    model_name TEXT, 
    last_sync TIMESTAMPTZ
);

-- 2. HIGH-FREQUENCY NUMERIC DATA (Heart Rate, Noise, HRV, etc.)
-- Using a code-based approach for performance
CREATE TABLE sensor_vitals (
    time TIMESTAMPTZ NOT NULL,
    device_id UUID NOT NULL REFERENCES devices(device_id),
    metric_type SMALLINT NOT NULL, -- 1:HR, 2:SDNN, 3:RMSSD, 4:dB_Ambient, 5:dB_Exposure
    val REAL NOT NULL
);
SELECT create_hypertable('sensor_vitals', 'time');

-- 3. SPATIAL DATA (GPS)
CREATE TABLE sensor_location (
    time TIMESTAMPTZ NOT NULL,
    device_id UUID NOT NULL REFERENCES devices(device_id),
    coords GEOGRAPHY(POINT, 4326) NOT NULL,
    accuracy REAL,
    motion_context TEXT -- 'walking', 'stationary', 'automotive'
);
SELECT create_hypertable('sensor_location', 'time');

-- 4. DISCRETE EVENTS & USAGE (App Usage, Screen Events, Notifications)
CREATE TABLE user_events (
    time TIMESTAMPTZ NOT NULL,
    device_id UUID NOT NULL REFERENCES devices(device_id),
    event_type TEXT NOT NULL, -- 'notification', 'screen_on', 'app_usage'
    label TEXT,              -- 'Instagram', 'Pickup'
    duration_sec INT,        -- For app usage/screen time
    metadata JSONB           -- For extra context
);
SELECT create_hypertable('user_events', 'time');

-- 5. DAILY HEALTH SNAPSHOTS (Steps, Active Energy, Stand/Exercise)
CREATE TABLE daily_summaries (
    date DATE NOT NULL,
    device_id UUID NOT NULL REFERENCES devices(device_id),
    steps INT,
    active_energy_kcal REAL,
    exercise_min INT,
    sleep_start TIMESTAMPTZ,
    sleep_end TIMESTAMPTZ,
    PRIMARY KEY (date, device_id)
);
