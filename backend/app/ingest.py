import json
import logging
from pathlib import Path
from typing import Optional
from psycopg2.extras import execute_values

from .db import get_connection, release_connection
from .models import Batch


_repo_root = Path(__file__).resolve().parents[2]
_log_dir = _repo_root / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("ingest")
if not logger.handlers:
    error_handler = logging.FileHandler(_log_dir / "ingest_errors.log")
    error_formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    error_handler.setFormatter(error_formatter)
    error_handler.setLevel(logging.ERROR)

    audit_handler = logging.FileHandler(_log_dir / "ingest_audit.log")
    audit_formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    audit_handler.setFormatter(audit_formatter)
    audit_handler.setLevel(logging.INFO)

    logger.addHandler(error_handler)
    logger.addHandler(audit_handler)
    logger.setLevel(logging.INFO)


CANONICAL_METRICS = [
    (1, "heart_rate_bpm", "count/min", "Heart rate in beats per minute"),
    (2, "heart_rate_variability_sdnn_ms", "ms", "Heart rate variability SDNN in milliseconds"),
    (10, "environmental_noise_db", "dBA", "Ambient or environmental noise level"),
    (20, "steps", "count", "Step count increment"),
    (21, "distance_m", "meter", "Distance walked/running"),
]


def ingest_batch(batch: Batch) -> None:
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        _ensure_metric_catalog(cursor)

        base_device_id = batch.metadata.device_id.lower()
        user_id = batch.metadata.user_id.lower()
        model_name = batch.metadata.model_name

        # Ensure user and device rows exist
        cursor.execute(
            "INSERT INTO users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
            (user_id,),
        )

        cursor.execute(
            "INSERT INTO devices (id, user_id, model_name, last_sync) "
            "VALUES (%s, %s, %s, NOW()) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  user_id = EXCLUDED.user_id, "
            "  model_name = COALESCE(EXCLUDED.model_name, devices.model_name), "
            "  last_sync = NOW()",
            (base_device_id, user_id, model_name),
        )

        vitals = []
        gps_points = []
        motion_events = []
        audio_events = []
        events = []
        seen_motion = set()
        supports_session_key = _sessions_has_session_key(cursor)
        active_sessions = {base_device_id: _find_active_session(cursor, user_id, base_device_id)}
        active_sessions_by_key = {}
        known_device_ids = {base_device_id}

        allowed_metrics = {1, 2, 10, 20, 21}

        for reading in sorted(batch.data, key=lambda item: item.t):
            reading_device_id = _reading_device_id(reading, base_device_id)
            session_key = _reading_session_key(reading) if supports_session_key else None
            if reading_device_id not in known_device_ids:
                cursor.execute(
                    "INSERT INTO devices (id, user_id, model_name, last_sync) "
                    "VALUES (%s, %s, %s, NOW()) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "  user_id = EXCLUDED.user_id, "
                    "  model_name = COALESCE(EXCLUDED.model_name, devices.model_name), "
                    "  last_sync = NOW()",
                    (reading_device_id, user_id, None),
                )
                known_device_ids.add(reading_device_id)

            if session_key:
                if session_key not in active_sessions_by_key:
                    active_sessions_by_key[session_key] = _find_active_session_by_key(cursor, user_id, session_key)
                active_session = active_sessions_by_key[session_key]
                session_id = active_session["id"] if active_session else _find_session_for_time_by_key(
                    cursor,
                    user_id,
                    session_key,
                    reading.t,
                )
            else:
                if reading_device_id not in active_sessions:
                    active_sessions[reading_device_id] = _find_active_session(cursor, user_id, reading_device_id)

                active_session = active_sessions[reading_device_id]
                session_id = active_session["id"] if active_session else _find_session_for_time(
                    cursor,
                    user_id,
                    reading_device_id,
                    reading.t,
                )

            if reading.type == "vital":
                if reading.code not in allowed_metrics:
                    continue
                vitals.append(
                    (
                        reading.t,
                        user_id,
                        reading_device_id,
                        session_id,
                        reading.code,
                        reading.val,
                        json.dumps(getattr(reading, "metadata", None) or {}),
                    )
                )
            elif reading.type == "gps":
                gps_points.append(
                    (
                        reading.t,
                        user_id,
                        reading_device_id,
                        session_id,
                        reading.lat,
                        reading.lon,
                        reading.acc,
                        None,
                        None,
                        json.dumps(reading.metadata or {}),
                    )
                )
                if getattr(reading, "motion_context", None):
                    motion_key = (reading.t, reading_device_id, reading.motion_context)
                    if motion_key not in seen_motion:
                        seen_motion.add(motion_key)
                        motion_events.append(
                            (
                                reading.t,
                                user_id,
                                reading_device_id,
                                session_id,
                                reading.motion_context,
                                json.dumps({"source": "gps_payload"}),
                            )
                        )
            elif reading.type == "event":
                label = reading.label
                meta = reading.metadata or {}
                if label == "session_marker":
                    marker = (reading.val_text or "").upper()
                    if marker == "START":
                        active_session = _get_or_create_session(
                            cursor,
                            user_id,
                            base_device_id,
                            reading.t,
                            session_key,
                            supports_session_key,
                        )
                        session_id = active_session["id"]
                        active_sessions[reading_device_id] = active_session
                        if session_key:
                            active_sessions_by_key[session_key] = active_session
                    elif marker == "END":
                        if not active_session:
                            if session_key:
                                active_session = _find_latest_session_by_key(cursor, user_id, session_key, reading.t)
                            else:
                                active_session = _find_latest_session_by_device(cursor, user_id, reading_device_id, reading.t)

                        if active_session:
                            session_id = active_session["id"]
                            _close_session(cursor, active_session["id"], reading.t)
                            _backfill_session_rows(
                                cursor,
                                active_session["id"],
                                user_id,
                                reading_device_id,
                                active_session["started_at"],
                                reading.t,
                            )

                if label == "motion_context":
                    motion_value = reading.val_text or meta.get("context", "unknown")
                    motion_key = (reading.t, reading_device_id, motion_value)
                    if motion_key not in seen_motion:
                        seen_motion.add(motion_key)
                        motion_events.append(
                            (
                                reading.t,
                                user_id,
                                reading_device_id,
                                session_id,
                                motion_value,
                                json.dumps(meta),
                            )
                        )
                elif label == "audio_context":
                    audio_events.append(
                        (
                            reading.t,
                            user_id,
                            reading_device_id,
                            session_id,
                            reading.val_text or "unknown",
                            _safe_float(meta.get("db")),
                            _safe_float(meta.get("confidence")),
                            meta.get("ai_label"),
                            _safe_float(meta.get("ai_confidence")),
                            json.dumps(meta),
                        )
                    )
                else:
                    events.append(
                        (
                            reading.t,
                            user_id,
                            reading_device_id,
                            session_id,
                            reading.label,
                            reading.val_text,
                            json.dumps(meta),
                        )
                    )

                if label == "session_marker" and (reading.val_text or "").upper() == "END":
                    active_sessions[reading_device_id] = None
                    if session_key:
                        active_sessions_by_key[session_key] = None

        if vitals:
            execute_values(
                cursor,
                "INSERT INTO vitals (time, user_id, device_id, session_id, metric_code, value, metadata) VALUES %s ON CONFLICT DO NOTHING",
                vitals,
            )

        if gps_points:
            execute_values(
                cursor,
                "INSERT INTO gps (time, user_id, device_id, session_id, lat, lon, acc, coarse_lat, coarse_lon, metadata) VALUES %s ON CONFLICT DO NOTHING",
                gps_points,
            )

        if motion_events:
            execute_values(
                cursor,
                "INSERT INTO motion_events (time, user_id, device_id, session_id, context, metadata) VALUES %s ON CONFLICT DO NOTHING",
                motion_events,
            )

        if audio_events:
            execute_values(
                cursor,
                "INSERT INTO audio_events (time, user_id, device_id, session_id, label, db, confidence, ai_label, ai_confidence, metadata) VALUES %s ON CONFLICT DO NOTHING",
                audio_events,
            )

        if events:
            execute_values(
                cursor,
                "INSERT INTO events (time, user_id, device_id, session_id, label, val_text, metadata) VALUES %s ON CONFLICT DO NOTHING",
                events,
            )

        connection.commit()
        total_records = len(vitals) + len(gps_points) + len(motion_events) + len(audio_events) + len(events)
        logger.info("ingest_success device_id=%s records=%s", base_device_id, total_records)
    except Exception:
        if connection:
            connection.rollback()
        logger.exception("Ingestion failed for device_id=%s", getattr(batch.metadata, "device_id", "unknown"))
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_connection(connection)


def _safe_float(val):
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _ensure_metric_catalog(cursor) -> None:
    execute_values(
        cursor,
        "INSERT INTO metric_catalog (code, name, unit, description) VALUES %s ON CONFLICT (code) DO NOTHING",
        CANONICAL_METRICS,
    )


def _sessions_has_session_key(cursor) -> bool:
    cursor.execute(
        "SELECT EXISTS ("
        "  SELECT 1 FROM information_schema.columns "
        "  WHERE table_schema = 'public' "
        "    AND table_name = 'sessions' "
        "    AND column_name = 'session_key'"
        ")"
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _reading_device_id(reading, default_device_id: str) -> str:
    value = getattr(reading, "device_id", None)
    return (value or default_device_id).lower()


def _reading_session_key(reading) -> Optional[str]:
    metadata = getattr(reading, "metadata", None) or {}
    value = metadata.get("session_key")
    if not value:
        return None
    return str(value).lower()


def _find_active_session(cursor, user_id: str, device_id: str):
    cursor.execute(
        "SELECT id, started_at FROM sessions "
        "WHERE user_id = %s AND device_id = %s AND ended_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (user_id, device_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {"id": row[0], "started_at": row[1]}


def _find_active_session_by_key(cursor, user_id: str, session_key: str):
    cursor.execute(
        "SELECT id, started_at FROM sessions "
        "WHERE user_id = %s AND session_key = %s AND ended_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (user_id, session_key),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {"id": row[0], "started_at": row[1]}


def _find_session_for_time(cursor, user_id: str, device_id: str, reading_time: str):
    cursor.execute(
        "SELECT id FROM sessions "
        "WHERE user_id = %s AND device_id = %s "
        "  AND started_at <= %s::timestamptz "
        "  AND (ended_at IS NULL OR ended_at >= %s::timestamptz) "
        "ORDER BY started_at DESC LIMIT 1",
        (user_id, device_id, reading_time, reading_time),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _find_session_for_time_by_key(cursor, user_id: str, session_key: str, reading_time: str):
    cursor.execute(
        "SELECT id FROM sessions "
        "WHERE user_id = %s AND session_key = %s "
        "  AND started_at <= %s::timestamptz "
        "  AND (ended_at IS NULL OR ended_at >= %s::timestamptz) "
        "ORDER BY started_at DESC LIMIT 1",
        (user_id, session_key, reading_time, reading_time),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _find_latest_session_by_key(cursor, user_id: str, session_key: str, reading_time: str):
    cursor.execute(
        "SELECT id, started_at FROM sessions "
        "WHERE user_id = %s AND session_key = %s "
        "  AND started_at <= %s::timestamptz "
        "ORDER BY started_at DESC LIMIT 1",
        (user_id, session_key, reading_time),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {"id": row[0], "started_at": row[1]}


def _find_latest_session_by_device(cursor, user_id: str, device_id: str, reading_time: str):
    cursor.execute(
        "SELECT id, started_at FROM sessions "
        "WHERE user_id = %s AND device_id = %s "
        "  AND started_at <= %s::timestamptz "
        "ORDER BY started_at DESC LIMIT 1",
        (user_id, device_id, reading_time),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {"id": row[0], "started_at": row[1]}


def _get_or_create_session(
    cursor,
    user_id: str,
    device_id: str,
    started_at: str,
    session_key: Optional[str],
    supports_session_key: bool,
):
    if supports_session_key and session_key:
        cursor.execute(
            "SELECT id, started_at FROM sessions "
            "WHERE user_id = %s AND session_key = %s "
            "ORDER BY id DESC LIMIT 1",
            (user_id, session_key),
        )
    else:
        cursor.execute(
            "SELECT id, started_at FROM sessions "
            "WHERE user_id = %s AND device_id = %s AND started_at = %s::timestamptz "
            "ORDER BY id DESC LIMIT 1",
            (user_id, device_id, started_at),
        )
    existing = cursor.fetchone()
    if existing:
        return {"id": existing[0], "started_at": existing[1]}

    if supports_session_key:
        cursor.execute(
            "INSERT INTO sessions (user_id, device_id, started_at, ended_at, label, session_key) "
            "VALUES (%s, %s, %s::timestamptz, NULL, %s, %s) RETURNING id, started_at",
            (user_id, device_id, started_at, "study_session", session_key),
        )
    else:
        cursor.execute(
            "INSERT INTO sessions (user_id, device_id, started_at, ended_at, label) "
            "VALUES (%s, %s, %s::timestamptz, NULL, %s) RETURNING id, started_at",
            (user_id, device_id, started_at, "study_session"),
        )
    created = cursor.fetchone()
    return {"id": created[0], "started_at": created[1]}


def _close_session(cursor, session_id: int, ended_at: str) -> None:
    cursor.execute(
        "UPDATE sessions "
        "SET ended_at = CASE "
        "    WHEN ended_at IS NULL OR ended_at < %s::timestamptz THEN %s::timestamptz "
        "    ELSE ended_at "
        "END "
        "WHERE id = %s",
        (ended_at, ended_at, session_id),
    )


def _backfill_session_rows(cursor, session_id: int, user_id: str, device_id: str, started_at, ended_at: str) -> None:
    for table in ("vitals", "gps", "motion_events", "audio_events", "events"):
        cursor.execute(
            f"UPDATE {table} SET session_id = %s "
            f"WHERE session_id IS NULL "
            f"  AND user_id = %s "
            f"  AND device_id = %s "
            f"  AND time >= %s::timestamptz "
            f"  AND time <= %s::timestamptz",
            (session_id, user_id, device_id, started_at, ended_at),
        )
