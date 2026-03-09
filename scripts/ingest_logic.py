import psycopg2
from psycopg2.extras import execute_values
import json


def ingest_batch(connection, batch_data):
    cursor = connection.cursor()
    device_id = batch_data['metadata']['device_id']
    user_id = batch_data['metadata'].get('user_id', device_id)

    # Ensure user and device exist
    cursor.execute("INSERT INTO users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (user_id,))
    cursor.execute(
        "INSERT INTO devices (id, user_id, model_name, last_sync) VALUES (%s, %s, %s, NOW()) ON CONFLICT (id) DO UPDATE SET last_sync = NOW()",
        (device_id, user_id, batch_data['metadata'].get('model_name')),
    )

    vitals = []
    gps_points = []
    events = []

    allowed_metrics = {1, 2, 10, 20, 21}

    active_session_by_key = {}

    for reading in batch_data['data']:
        r_type = reading.get('type')
        timestamp = reading.get('t')
        reading_device_id = (reading.get('device_id') or device_id)
        session_key = ((reading.get('metadata') or {}).get('session_key') or '').lower() or None
        session_id = active_session_by_key.get(session_key) if session_key else None

        if r_type == 'event' and reading.get('label') == 'session_marker':
            marker = (reading.get('val_text') or '').upper()
            if marker == 'START' and session_key:
                cursor.execute(
                    "SELECT id FROM sessions WHERE user_id=%s AND session_key=%s ORDER BY id DESC LIMIT 1",
                    (user_id, session_key),
                )
                row = cursor.fetchone()
                if row:
                    session_id = row[0]
                else:
                    cursor.execute(
                        "INSERT INTO sessions (user_id, device_id, session_key, started_at, label) VALUES (%s, %s, %s, %s::timestamptz, %s) RETURNING id",
                        (user_id, device_id, session_key, timestamp, 'study_session'),
                    )
                    session_id = cursor.fetchone()[0]
                active_session_by_key[session_key] = session_id
            elif marker == 'END' and session_key:
                session_id = active_session_by_key.get(session_key)
                if session_id:
                    cursor.execute(
                        "UPDATE sessions SET ended_at = CASE WHEN ended_at IS NULL OR ended_at < %s::timestamptz THEN %s::timestamptz ELSE ended_at END WHERE id = %s",
                        (timestamp, timestamp, session_id),
                    )

        if r_type == 'vital' and reading.get('code') in allowed_metrics:
            vitals.append((timestamp, user_id, reading_device_id, None, reading['code'], reading['val'], json.dumps(reading.get('metadata', {}) or {})))

        elif r_type == 'gps':
            gps_points.append((timestamp, user_id, reading_device_id, None, reading['lat'], reading['lon'], reading.get('acc'), None, None, json.dumps(reading.get('metadata', {}))))

        elif r_type == 'event':
            label = reading.get('label')
            if label == 'motion_context':
                events.append((timestamp, user_id, reading_device_id, None, 'motion_context', reading.get('val_text'), json.dumps(reading.get('metadata', {}))))
            elif label == 'audio_context':
                meta = reading.get('metadata', {}) or {}
                events.append((timestamp, user_id, reading_device_id, None, 'audio_context', reading.get('val_text'), json.dumps(meta)))
            else:
                events.append((timestamp, user_id, reading_device_id, None, reading.get('label'), reading.get('val_text'), json.dumps(reading.get('metadata', {}))))

    if vitals:
        execute_values(cursor, "INSERT INTO vitals (time, user_id, device_id, session_id, metric_code, value, metadata) VALUES %s", vitals)

    if gps_points:
        execute_values(cursor, "INSERT INTO gps (time, user_id, device_id, session_id, lat, lon, acc, coarse_lat, coarse_lon, metadata) VALUES %s", gps_points)

    if events:
        execute_values(cursor, "INSERT INTO events (time, user_id, device_id, session_id, label, val_text, metadata) VALUES %s", events)

    for session_key, session_id in active_session_by_key.items():
        for table in ('vitals', 'gps', 'events'):
            cursor.execute(
                f"UPDATE {table} SET session_id = %s WHERE session_id IS NULL AND user_id = %s AND metadata->>'session_key' = %s",
                (session_id, user_id, session_key),
            )

    connection.commit()
    cursor.close()
    print(f"Successfully ingested {len(vitals) + len(gps_points) + len(events)} records.")
