import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg2
from fastapi.testclient import TestClient


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def run() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    load_env(repo_root / ".env")

    os.environ["POSTGRES_HOST"] = "localhost"
    os.environ["POSTGRES_PORT"] = "5433"
    os.environ["POSTGRES_DB"] = "sensing_db"
    os.environ["POSTGRES_USER"] = "postgres"
    os.environ["INGEST_API_KEY"] = os.getenv("INGEST_API_KEY", "dev_key")

    from backend.app.main import app

    conn = psycopg2.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
    )
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM vitals WHERE device_id='load-device'")
    before = cur.fetchone()[0]

    requests_ok = 0
    with TestClient(app) as client:
        for index in range(50):
            timestamp = (datetime.now(timezone.utc) + timedelta(milliseconds=index)).isoformat()
            payload = {
                "metadata": {"device_id": "load-device", "user_id": "load-user"},
                "data": [
                    {"type": "vital", "t": timestamp, "code": 1, "val": 70 + (index % 5)},
                    {"type": "gps", "t": timestamp, "lat": 22.3, "lon": 114.1, "acc": 5.0},
                    {
                        "type": "event",
                        "t": timestamp,
                        "label": "audio_context",
                        "val_text": "busy",
                        "metadata": {"db": "-40.0", "confidence": "0.5"},
                    },
                ],
            }
            response = client.post("/ingest", headers={"X-API-Key": os.environ["INGEST_API_KEY"]}, json=payload)
            if response.status_code == 200:
                requests_ok += 1

    cur.execute("SELECT count(*) FROM vitals WHERE device_id='load-device'")
    after = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM vitals WHERE device_id='load-device' AND metric_code NOT IN (1,10,20,21)")
    invalid = cur.fetchone()[0]

    cur.close()
    conn.close()

    print(
        json.dumps(
            {
                "requests_ok": requests_ok,
                "requests_total": 50,
                "vitals_before": before,
                "vitals_after": after,
                "invalid_metric_rows": invalid,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
