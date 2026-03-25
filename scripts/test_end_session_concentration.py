import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
ccot_output_dir = repo_root / "llm" / "CCoT" / "output"


def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env(repo_root / ".env")
sys.path.insert(0, str(repo_root))

from backend.app.concentration import process_next_pending_job  # noqa: E402
from backend.app.db import close_pool, get_connection, init_pool, release_connection  # noqa: E402
from backend.app.ingest import ingest_batch  # noqa: E402
from backend.app.models import Batch  # noqa: E402


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def build_dummy_payload(user_id: str, device_id: str) -> dict:
    start = datetime.now(timezone.utc) - timedelta(minutes=25)
    end = start + timedelta(minutes=20)

    data = [
        {"t": iso(start), "type": "event", "label": "session_marker", "val_text": "START"},
        {"t": iso(start + timedelta(minutes=1)), "type": "vital", "code": 1, "val": 72.0},
        {"t": iso(start + timedelta(minutes=2)), "type": "vital", "code": 20, "val": 4.0},
        {"t": iso(start + timedelta(minutes=3)), "type": "vital", "code": 21, "val": 6.5},
        {
            "t": iso(start + timedelta(minutes=2)),
            "type": "gps",
            "lat": 22.3193,
            "lon": 114.1694,
            "acc": 8.0,
            "motion_context": "stationary",
            "metadata": {"source": "core_location"},
        },
        {
            "t": iso(start + timedelta(minutes=10)),
            "type": "gps",
            "lat": 22.31935,
            "lon": 114.16938,
            "acc": 9.5,
            "motion_context": "stationary",
            "metadata": {"source": "core_location"},
        },
        {
            "t": iso(start + timedelta(minutes=5)),
            "type": "event",
            "label": "audio_context",
            "val_text": "quiet",
            "metadata": {
                "db": "41.2",
                "confidence": "0.83",
                "ai_label": "Silence",
                "ai_confidence": "0.81",
            },
        },
        {
            "t": iso(start + timedelta(minutes=7)),
            "type": "event",
            "label": "audio_context",
            "val_text": "busy",
            "metadata": {
                "db": "59.8",
                "confidence": "0.72",
                "ai_label": "Speech",
                "ai_confidence": "0.69",
            },
        },
        {
            # This one should be ignored by concentration calc due to confidence <= 0.5
            "t": iso(start + timedelta(minutes=8)),
            "type": "event",
            "label": "audio_context",
            "val_text": "busy",
            "metadata": {
                "db": "64.0",
                "confidence": "0.30",
                "ai_label": "Speech",
                "ai_confidence": "0.42",
            },
        },
        {"t": iso(start + timedelta(minutes=12)), "type": "event", "label": "motion_context", "val_text": "stationary"},
        {"t": iso(start + timedelta(minutes=18)), "type": "event", "label": "motion_context", "val_text": "walking"},
        {"t": iso(end), "type": "event", "label": "session_marker", "val_text": "END"},
    ]

    return {
        "metadata": {
            "user_id": user_id,
            "device_id": device_id,
            "version": "test",
            "model_name": "simulator",
        },
        "data": data,
    }


def fetch_latest_session_id(user_id: str, device_id: str) -> int:
    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM sessions WHERE user_id = %s AND device_id = %s ORDER BY started_at DESC LIMIT 1",
            (user_id, device_id),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError("No session row found after ingest")
        return int(row[0])
    finally:
        if cur:
            cur.close()
        if conn:
            release_connection(conn)


def fetch_concentration_row(session_id: int) -> dict:
    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT session_id, status, score, reason, model, error_message, triggered_at, processing_started_at, completed_at "
            "FROM session_concentration_analysis WHERE session_id = %s",
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError("No concentration analysis row found")
        return {
            "session_id": row[0],
            "status": row[1],
            "score": row[2],
            "reason": row[3],
            "model": row[4],
            "error_message": row[5],
            "triggered_at": row[6].isoformat() if row[6] else None,
            "processing_started_at": row[7].isoformat() if row[7] else None,
            "completed_at": row[8].isoformat() if row[8] else None,
        }
    finally:
        if cur:
            cur.close()
        if conn:
            release_connection(conn)


def main() -> None:
    provider = (os.getenv("LLM_PROVIDER") or "openai").strip().lower()
    if provider == "openai" and not os.getenv("LLM_API_KEY"):
        raise RuntimeError("LLM_PROVIDER=openai but LLM_API_KEY is missing in .env.")
    if provider == "snowflake":
        required = [
            "SNOWFLAKE_ACCOUNT",
            "SNOWFLAKE_USER",
            "SNOWFLAKE_USER_PASSWORD",
            "SNOWFLAKE_ROLE",
            "SNOWFLAKE_DATABASE",
            "SNOWFLAKE_SCHEMA",
            "SNOWFLAKE_WAREHOUSE",
        ]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise RuntimeError(f"LLM_PROVIDER=snowflake but missing env vars: {', '.join(missing)}")

    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    device_id = f"test-device-{uuid.uuid4().hex[:8]}"

    ccot_output_dir.mkdir(parents=True, exist_ok=True)

    print("Building dummy payload...")
    payload = build_dummy_payload(user_id=user_id, device_id=device_id)
    print(json.dumps(payload, indent=2))

    print("\nIngesting payload...")
    init_pool()
    try:
        batch = Batch.model_validate(payload)
        ingest_batch(batch)

        session_id = fetch_latest_session_id(user_id=user_id, device_id=device_id)
        print(f"Session created/closed: {session_id}")

        print("Running concentration worker for one pending job...")
        worked = process_next_pending_job()
        if not worked:
            raise RuntimeError("No pending concentration job found; END marker may not have queued it.")

        result = fetch_concentration_row(session_id=session_id)
        print("\nConcentration analysis row:")
        print(json.dumps(result, indent=2))

        output_data = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "user_id": user_id,
            "device_id": device_id,
            "session_id": session_id,
            "dummy_payload": payload,
            "analysis_result": result,
        }
        output_path = ccot_output_dir / f"dummy_concentration_result_session_{session_id}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        print(f"\nSaved dummy output to: {output_path}")

        if result["status"] != "done":
            raise RuntimeError(f"Analysis not done. Status={result['status']} error={result['error_message']}")

        print("\nSUCCESS: LLM call completed and concentration output was stored.")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
