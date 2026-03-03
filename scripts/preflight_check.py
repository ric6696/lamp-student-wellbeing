import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from fastapi.testclient import TestClient


def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def run() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    load_env(repo_root / ".env")

    # Force local docker DB for pre-flight.
    os.environ["POSTGRES_HOST"] = os.getenv("POSTGRES_HOST_LOCAL", "localhost")
    os.environ["POSTGRES_PORT"] = os.getenv("POSTGRES_PORT_LOCAL", "5433")
    os.environ["POSTGRES_DB"] = os.getenv("POSTGRES_DB_LOCAL", os.getenv("POSTGRES_DB", "sensing_db"))
    os.environ["POSTGRES_USER"] = os.getenv("POSTGRES_USER_LOCAL", "postgres")
    os.environ["INGEST_API_KEY"] = os.getenv("INGEST_API_KEY", "dev_key")

    from backend.app.main import app

    results: dict = {}

    with TestClient(app) as client:
        health = client.get("/health")
        results["health"] = {"status": health.status_code, "body": health.json()}

        payload_empty = {"metadata": {"device_id": "preflight-device"}, "data": []}
        r = client.post("/ingest", headers={"X-API-Key": os.environ["INGEST_API_KEY"]}, json=payload_empty)
        results["empty_payload"] = {"status": r.status_code, "body": r.json()}

        payload_missing = {"metadata": {}, "data": []}
        r = client.post("/ingest", headers={"X-API-Key": os.environ["INGEST_API_KEY"]}, json=payload_missing)
        results["missing_device_id"] = {"status": r.status_code, "body": r.json()}

        r = client.post("/ingest", headers={"X-API-Key": "bad"}, json=payload_empty)
        results["bad_api_key"] = {"status": r.status_code, "body": r.json()}

        now = datetime.now(timezone.utc).isoformat()
        payload_real = {
            "metadata": {"device_id": "preflight-device", "user_id": "preflight-user", "model_name": "iPhone"},
            "data": [
                {"type": "vital", "t": now, "code": 1, "val": 83.0},
                {"type": "vital", "t": now, "code": 20, "val": 12.0},
                {
                    "type": "gps",
                    "t": now,
                    "lat": 22.3193,
                    "lon": 114.1694,
                    "acc": 8.0,
                    "motion_context": "walking",
                    "metadata": {"place_category": "commute"},
                },
                {
                    "type": "event",
                    "t": now,
                    "label": "motion_context",
                    "val_text": "walking",
                    "metadata": {"source": "coremotion"},
                },
                {
                    "type": "event",
                    "t": now,
                    "label": "audio_context",
                    "val_text": "busy",
                    "metadata": {
                        "db": "-35.2",
                        "confidence": "0.83",
                        "ai_label": "traffic",
                        "ai_confidence": "0.77",
                    },
                },
            ],
        }
        r = client.post("/ingest", headers={"X-API-Key": os.environ["INGEST_API_KEY"]}, json=payload_real)
        results["reality_payload"] = {"status": r.status_code, "body": r.json()}

        r1 = client.post("/ingest", headers={"X-API-Key": os.environ["INGEST_API_KEY"]}, json=payload_real)
        r2 = client.post("/ingest", headers={"X-API-Key": os.environ["INGEST_API_KEY"]}, json=payload_real)
        results["duplicate_send"] = {"status1": r1.status_code, "status2": r2.status_code}

    conn = psycopg2.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
    )
    cur = conn.cursor()

    checks = {
        "counts": "SELECT (SELECT count(*) FROM vitals), (SELECT count(*) FROM gps), (SELECT count(*) FROM events), (SELECT count(*) FROM motion_events), (SELECT count(*) FROM audio_events)",
        "device_rows": "SELECT count(*) FROM devices WHERE id='preflight-device'",
        "user_rows": "SELECT count(*) FROM users WHERE id='preflight-user'",
        "invalid_metric_rows": "SELECT count(*) FROM vitals WHERE metric_code NOT IN (1,10,20,21)",
        "orphan_vitals": "SELECT count(*) FROM vitals v LEFT JOIN devices d ON v.device_id = d.id WHERE d.id IS NULL",
        "dup_vitals": "SELECT count(*) FROM (SELECT device_id, metric_code, time, count(*) c FROM vitals GROUP BY 1,2,3 HAVING count(*) > 1) t",
        "latest_preflight_vitals": "SELECT metric_code, value FROM vitals WHERE device_id='preflight-device' ORDER BY time DESC, metric_code LIMIT 6",
        "latest_preflight_gps_place": "SELECT metadata->>'place_category' FROM gps WHERE device_id='preflight-device' ORDER BY time DESC LIMIT 1",
        "latest_motion": "SELECT context FROM motion_events WHERE device_id='preflight-device' ORDER BY time DESC LIMIT 1",
        "latest_audio": "SELECT label, db, confidence, ai_label, ai_confidence FROM audio_events WHERE device_id='preflight-device' ORDER BY time DESC LIMIT 1",
    }

    for key, sql in checks.items():
        cur.execute(sql)
        results[key] = cur.fetchall()

    cur.close()
    conn.close()

    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
