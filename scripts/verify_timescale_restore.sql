-- Post-restore verification checklist SQL
-- Usage:
--   psql -h <host> -U <user> -d <db> -f scripts/verify_timescale_restore.sql

\echo '--- Extensions ---'
SELECT extname FROM pg_extension WHERE extname IN ('timescaledb','postgis') ORDER BY extname;

\echo '--- Required tables ---'
SELECT table_name
FROM information_schema.tables
WHERE table_schema='public'
  AND table_name IN ('users','devices','sessions','metric_catalog','vitals','gps','events','motion_events','audio_events')
ORDER BY table_name;

\echo '--- Row counts ---'
SELECT 'users' AS table_name, count(*)::bigint AS rows FROM users
UNION ALL SELECT 'devices', count(*)::bigint FROM devices
UNION ALL SELECT 'sessions', count(*)::bigint FROM sessions
UNION ALL SELECT 'metric_catalog', count(*)::bigint FROM metric_catalog
UNION ALL SELECT 'vitals', count(*)::bigint FROM vitals
UNION ALL SELECT 'gps', count(*)::bigint FROM gps
UNION ALL SELECT 'events', count(*)::bigint FROM events
UNION ALL SELECT 'motion_events', count(*)::bigint FROM motion_events
UNION ALL SELECT 'audio_events', count(*)::bigint FROM audio_events
ORDER BY table_name;

\echo '--- Integrity checks ---'
SELECT count(*) AS invalid_metric_rows FROM vitals WHERE metric_code NOT IN (1,10,20,21);
SELECT count(*) AS orphan_vitals FROM vitals v LEFT JOIN devices d ON v.device_id=d.id WHERE d.id IS NULL;
SELECT count(*) AS orphan_gps FROM gps g LEFT JOIN devices d ON g.device_id=d.id WHERE d.id IS NULL;
SELECT count(*) AS orphan_events FROM events e LEFT JOIN devices d ON e.device_id=d.id WHERE d.id IS NULL;
SELECT count(*) AS duplicate_vital_keys
FROM (
    SELECT device_id, metric_code, time, count(*) c
    FROM vitals
    GROUP BY 1,2,3
    HAVING count(*) > 1
) t;

\echo '--- Latest records sanity ---'
SELECT time, device_id, metric_code, value FROM vitals ORDER BY time DESC LIMIT 10;
SELECT time, device_id, lat, lon FROM gps ORDER BY time DESC LIMIT 10;
SELECT time, device_id, label, val_text FROM events ORDER BY time DESC LIMIT 10;
SELECT time, device_id, context FROM motion_events ORDER BY time DESC LIMIT 10;
SELECT time, device_id, label, db, confidence FROM audio_events ORDER BY time DESC LIMIT 10;
