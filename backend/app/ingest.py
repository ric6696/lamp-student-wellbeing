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

        device_id = batch.metadata.device_id
        user_id = batch.metadata.user_id or device_id
        model_name = batch.metadata.model_name

        cursor.execute(
            "INSERT INTO devices (device_id, user_id, model_name, last_sync) "
            "VALUES (%s, %s, %s, NOW()) "
            "ON CONFLICT (device_id) DO NOTHING",
            (device_id, user_id, model_name),
        )

        vitals = []
        locations = []
        events = []

        for reading in batch.data:
            if reading.type == "vital":
                vitals.append((reading.t, device_id, reading.code, reading.val))
            elif reading.type == "gps":
                point = f"POINT({reading.lon} {reading.lat})"
                locations.append((reading.t, device_id, point, reading.acc))
            elif reading.type == "event":
                events.append(
                    (
                        reading.t,
                        device_id,
                        reading.label,
                        reading.val_text,
                        json.dumps(reading.metadata or {}),
                    )
                )

        if vitals:
            execute_values(
                cursor,
                "INSERT INTO sensor_vitals (time, device_id, metric_type, val) VALUES %s",
                vitals,
            )

        if locations:
            execute_values(
                cursor,
                "INSERT INTO sensor_location (time, device_id, coords, accuracy) VALUES %s",
                locations,
                template="(%s, %s, ST_GeogFromText(%s), %s)",
            )

        if events:
            execute_values(
                cursor,
                "INSERT INTO user_events (time, device_id, event_type, label, metadata) VALUES %s",
                events,
            )

        connection.commit()
        total_records = len(vitals) + len(locations) + len(events)
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
