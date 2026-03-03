import json
import logging
from pathlib import Path
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


def ingest_batch(batch: Batch) -> None:
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()

        device_id = batch.metadata.device_id.lower()
        user_id = (batch.metadata.user_id or device_id).lower()
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
            (device_id, user_id, model_name),
        )

        vitals = []
        gps_points = []
        motion_events = []
        audio_events = []
        events = []
        seen_motion = set()

        allowed_metrics = {1, 10, 20, 21}

        for reading in batch.data:
            if reading.type == "vital":
                if reading.code not in allowed_metrics:
                    continue
                vitals.append(
                    (
                        reading.t,
                        user_id,
                        device_id,
                        None,  # session_id not provided in payload
                        reading.code,
                        reading.val,
                        json.dumps({}),
                    )
                )
            elif reading.type == "gps":
                gps_points.append(
                    (
                        reading.t,
                        user_id,
                        device_id,
                        None,
                        reading.lat,
                        reading.lon,
                        reading.acc,
                        None,
                        None,
                        json.dumps(reading.metadata or {}),
                    )
                )
                if getattr(reading, "motion_context", None):
                    motion_key = (reading.t, device_id, reading.motion_context)
                    if motion_key not in seen_motion:
                        seen_motion.add(motion_key)
                        motion_events.append(
                            (
                                reading.t,
                                user_id,
                                device_id,
                                None,
                                reading.motion_context,
                                json.dumps({"source": "gps_payload"}),
                            )
                        )
            elif reading.type == "event":
                label = reading.label
                meta = reading.metadata or {}
                if label == "motion_context":
                    motion_value = reading.val_text or meta.get("context", "unknown")
                    motion_key = (reading.t, device_id, motion_value)
                    if motion_key not in seen_motion:
                        seen_motion.add(motion_key)
                        motion_events.append(
                            (
                                reading.t,
                                user_id,
                                device_id,
                                None,
                                motion_value,
                                json.dumps(meta),
                            )
                        )
                elif label == "audio_context":
                    audio_events.append(
                        (
                            reading.t,
                            user_id,
                            device_id,
                            None,
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
                            device_id,
                            None,
                            reading.label,
                            reading.val_text,
                            json.dumps(meta),
                        )
                    )

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
        logger.info("ingest_success device_id=%s records=%s", device_id, total_records)
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
