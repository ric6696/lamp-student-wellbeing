import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .concentration import ConcentrationWorker, bootstrap_concentration_schema
from .config import get_cors_origins, settings
from .db import close_pool, get_connection, init_pool, release_connection
from .ingest import ingest_batch
from .models import Batch

app = FastAPI(title="LAMP Ingestion API")
_concentration_worker = ConcentrationWorker()

_repo_root = Path(__file__).resolve().parents[2]
_log_dir = _repo_root / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_user_response_path = _repo_root / "llm" / "StudySessionAnalyst" / "user_response_to_concentration.json"
_concentration_output_path = _repo_root / "llm" / "CCoT" / "output" / "concentration_analysis_results.json"
_pre_session_context_path = _repo_root / "llm" / "CCoT" / "output" / "pre_session_context.json"
_analyst_script_path = _repo_root / "llm" / "StudySessionAnalyst" / "analyst.py"

http_logger = logging.getLogger("ingest.http")
if not http_logger.handlers:
    handler = logging.FileHandler(_log_dir / "ingest_http.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    http_logger.addHandler(handler)
    http_logger.setLevel(logging.INFO)

analysis_logger = logging.getLogger("ingest.analysis")
if not analysis_logger.handlers:
    handler = logging.FileHandler(_log_dir / "analysis_trigger.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    analysis_logger.addHandler(handler)
    analysis_logger.setLevel(logging.INFO)


@app.on_event("startup")
def startup() -> None:
    init_pool()
    bootstrap_concentration_schema()
    _concentration_worker.start()


@app.on_event("shutdown")
def shutdown() -> None:
    _concentration_worker.stop()
    close_pool()

cors_origins = get_cors_origins()
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def log_ingest_requests(request: Request, call_next):
    if request.url.path != "/ingest":
        return await call_next(request)

    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    http_logger.info(
        "ingest_http method=%s path=%s status=%s content_length=%s duration_ms=%.2f",
        request.method,
        request.url.path,
        response.status_code,
        request.headers.get("content-length", "-"),
        duration_ms,
    )
    return response


def require_api_key(x_api_key: str = Header(None)) -> None:
    expected = settings.ingest_api_key
    if not expected:
        raise HTTPException(status_code=500, detail="Server API key not configured")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _safe_mtime(path: Path) -> float | None:
    if not path.exists():
        return None
    return path.stat().st_mtime


def _json_file_ready(path: Path, required_keys: tuple[str, ...] = ()) -> tuple[bool, str]:
    if not path.exists():
        return False, f"missing:{path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"invalid_json:{path.name}:{exc}"

    if required_keys and isinstance(payload, dict):
        missing = [key for key in required_keys if key not in payload]
        if missing:
            return False, f"missing_keys:{path.name}:{','.join(missing)}"

    return True, "ready"


def _parse_json_object(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _outputs_ready_for_analyst(
    user_response_mtime: float,
    concentration_baseline_mtime: float | None,
    pre_session_baseline_mtime: float | None,
) -> tuple[bool, str]:
    # We only need valid, readable inputs. Freshness checks caused valid runs to be skipped.
    concentration_ok, concentration_reason = _json_file_ready(_concentration_output_path)
    if not concentration_ok:
        return False, concentration_reason

    pre_session_ok, pre_session_reason = _json_file_ready(
        _pre_session_context_path,
        required_keys=("activity_context", "environment_context", "mental_readiness"),
    )
    if not pre_session_ok:
        return False, pre_session_reason

    user_response_ok, user_response_reason = _json_file_ready(_user_response_path)
    if not user_response_ok:
        return False, user_response_reason

    return True, "ready"


def _run_analyst_after_session_review(
    user_response_mtime: float,
    concentration_baseline_mtime: float | None,
    pre_session_baseline_mtime: float | None,
) -> None:
    # Wait for concentration and pre-session context to be refreshed after user response submission.
    max_wait_seconds = 90
    wait_step = 3
    waited = 0
    last_reason = "unknown"

    while waited < max_wait_seconds:
        if not _user_response_path.exists():
            last_reason = f"missing_user_response:{_user_response_path}"
        else:
            ready, reason = _outputs_ready_for_analyst(
                user_response_mtime,
                concentration_baseline_mtime,
                pre_session_baseline_mtime,
            )
            last_reason = reason
            if ready:
                break

        time.sleep(wait_step)
        waited += wait_step

    ready, reason = _outputs_ready_for_analyst(
        user_response_mtime,
        concentration_baseline_mtime,
        pre_session_baseline_mtime,
    )
    if not ready:
        analysis_logger.warning(
            "skip_analyst_run reason=%s waited_seconds=%s user_response_mtime=%s concentration_exists=%s pre_session_exists=%s",
            reason if reason else last_reason,
            waited,
            user_response_mtime,
            _concentration_output_path.exists(),
            _pre_session_context_path.exists(),
        )
        return

    if not _user_response_path.exists():
        analysis_logger.warning("skip_analyst_run reason=missing_user_response path=%s", _user_response_path)
        return

    cmd = [
        sys.executable,
        str(_analyst_script_path),
        "--concentration",
        "CCoT/output/concentration_analysis_results.json",
        "--user-response",
        "StudySessionAnalyst/user_response_to_concentration.json",
        "--pre-session-questions",
        "CCoT/output/pre_session_context.json",
        "--output",
        "StudySessionAnalyst/discrepancy_analysis_results.json",
    ]

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(_repo_root / "llm"),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        analysis_logger.info(
            "analyst_run_finished returncode=%s stdout=%s stderr=%s",
            completed.returncode,
            (completed.stdout or "").strip(),
            (completed.stderr or "").strip(),
        )
    except Exception as exc:
        analysis_logger.exception("analyst_run_exception error=%s", exc)


@app.post("/ingest")
async def ingest(payload: Batch, background_tasks: BackgroundTasks, _: None = Depends(require_api_key)):
    background_tasks.add_task(ingest_batch, payload)
    return {"status": "accepted", "records": len(payload.data)}


@app.post("/session-review")
async def save_session_review(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    _: None = Depends(require_api_key),
):
    concentration_baseline_mtime = _safe_mtime(_concentration_output_path)
    pre_session_baseline_mtime = _safe_mtime(_pre_session_context_path)

    _atomic_write_json(_user_response_path, payload)
    user_response_mtime = _user_response_path.stat().st_mtime
    background_tasks.add_task(
        _run_analyst_after_session_review,
        user_response_mtime,
        concentration_baseline_mtime,
        pre_session_baseline_mtime,
    )
    return {"status": "saved", "path": str(_user_response_path)}


@app.get("/latest-concentration-report")
async def latest_concentration_report(_: None = Depends(require_api_key)):
    if not _concentration_output_path.exists():
        raise HTTPException(status_code=404, detail="No concentration report available yet")

    try:
        payload = json.loads(_concentration_output_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read concentration report: {exc}")

    return payload


@app.get("/health")
async def health():
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_connection(connection)


@app.get("/sessions/{session_id}/concentration")
async def get_session_concentration(session_id: int):
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT session_id, status, score, reason, model, llm_raw_response, error_message, triggered_at, processing_started_at, completed_at "
            "FROM session_concentration_analysis WHERE session_id = %s",
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No concentration analysis found for this session")

        llm_parsed = _parse_json_object(row[5])
        payload: dict[str, Any] = {
            "session_id": row[0],
            "status": row[1],
            "score": row[2],
            "reason": row[3],
            "model": row[4],
            "error_message": row[6],
            "triggered_at": row[7],
            "processing_started_at": row[8],
            "completed_at": row[9],
        }

        if llm_parsed:
            payload["phase_1"] = llm_parsed.get("phase_1")
            payload["phase_2"] = llm_parsed.get("phase_2")
            payload["phase_3"] = llm_parsed.get("phase_3")
            if payload.get("score") is None:
                payload["score"] = llm_parsed.get("score")
            if not payload.get("reason"):
                payload["reason"] = llm_parsed.get("reason")

        return payload
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_connection(connection)


@app.get("/users/{user_id}/sessions/latest/concentration")
async def get_latest_user_session_concentration(user_id: str, _: None = Depends(require_api_key)):
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT s.id, s.started_at, s.ended_at,
                   a.status, a.score, a.reason, a.model,
                   a.llm_raw_response, a.error_message,
                   a.triggered_at, a.processing_started_at, a.completed_at
            FROM sessions s
            LEFT JOIN session_concentration_analysis a
              ON a.session_id = s.id
            WHERE s.user_id = %s
              AND s.ended_at IS NOT NULL
            ORDER BY s.ended_at DESC
            LIMIT 1
            """,
            (user_id.lower(),),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No completed sessions found for this user")

        llm_parsed = _parse_json_object(row[7])
        payload: dict[str, Any] = {
            "session_id": row[0],
            "started_at": row[1],
            "ended_at": row[2],
            "status": row[3] or "pending",
            "score": row[4],
            "reason": row[5],
            "model": row[6],
            "error_message": row[8],
            "triggered_at": row[9],
            "processing_started_at": row[10],
            "completed_at": row[11],
        }

        if llm_parsed:
            payload["phase_1"] = llm_parsed.get("phase_1")
            payload["phase_2"] = llm_parsed.get("phase_2")
            payload["phase_3"] = llm_parsed.get("phase_3")
            if payload.get("score") is None:
                payload["score"] = llm_parsed.get("score")
            if not payload.get("reason"):
                payload["reason"] = llm_parsed.get("reason")

        return payload
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_connection(connection)


@app.get("/sessions/by-key/{session_key}/concentration-report")
async def get_concentration_report_by_session_key(session_key: str, _: None = Depends(require_api_key)):
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.columns "
            "  WHERE table_schema = 'public' "
            "    AND table_name = 'sessions' "
            "    AND column_name = 'session_key'"
            ")"
        )
        supports_session_key = bool(cursor.fetchone()[0])
        if not supports_session_key:
            raise HTTPException(status_code=400, detail="Session-key lookup is unavailable on this database schema")

        cursor.execute(
            "SELECT id, started_at, ended_at FROM sessions "
            "WHERE session_key = %s "
            "ORDER BY started_at DESC LIMIT 1",
            (session_key.lower(),),
        )
        session_row = cursor.fetchone()
        if not session_row:
            raise HTTPException(status_code=404, detail="No session found for this session_key")

        matched_session_id, started_at, ended_at = session_row

        cursor.execute(
            "SELECT session_id, status, score, reason, model, llm_raw_response, error_message, triggered_at, processing_started_at, completed_at "
            "FROM session_concentration_analysis WHERE session_id = %s",
            (matched_session_id,),
        )
        analysis_row = cursor.fetchone()

        if not analysis_row:
            return {
                "session_id": matched_session_id,
                "session_key": session_key.lower(),
                "started_at": started_at,
                "ended_at": ended_at,
                "status": "pending",
            }

        llm_parsed = _parse_json_object(analysis_row[5])
        response: dict[str, Any] = {
            "session_id": analysis_row[0],
            "session_key": session_key.lower(),
            "status": analysis_row[1],
            "score": analysis_row[2],
            "reason": analysis_row[3],
            "model": analysis_row[4],
            "error_message": analysis_row[6],
            "triggered_at": analysis_row[7],
            "processing_started_at": analysis_row[8],
            "completed_at": analysis_row[9],
            "started_at": started_at,
            "ended_at": ended_at,
        }

        if llm_parsed:
            response["phase_1"] = llm_parsed.get("phase_1")
            response["phase_2"] = llm_parsed.get("phase_2")
            response["phase_3"] = llm_parsed.get("phase_3")
            if response.get("score") is None:
                response["score"] = llm_parsed.get("score")
            if not response.get("reason"):
                response["reason"] = llm_parsed.get("reason")

        return response
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_connection(connection)


@app.get("/users/{user_id}/sessions")
async def list_user_sessions(user_id: str, limit: int = 50, _: None = Depends(require_api_key)):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")

    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.columns "
            "  WHERE table_schema = 'public' "
            "    AND table_name = 'sessions' "
            "    AND column_name = 'session_key'"
            ")"
        )
        supports_session_key = bool(cursor.fetchone()[0])

        if supports_session_key:
            cursor.execute(
                """
                SELECT s.id, s.session_key, s.started_at, s.ended_at,
                       a.status, a.score, a.reason, a.model,
                       a.llm_raw_response, a.error_message,
                       a.triggered_at, a.processing_started_at, a.completed_at
                FROM sessions s
                LEFT JOIN session_concentration_analysis a
                  ON a.session_id = s.id
                WHERE s.user_id = %s
                  AND s.ended_at IS NOT NULL
                ORDER BY s.ended_at DESC, s.started_at DESC
                LIMIT %s
                """,
                (user_id.lower(), limit),
            )
        else:
            cursor.execute(
                """
                SELECT s.id, NULL AS session_key, s.started_at, s.ended_at,
                       a.status, a.score, a.reason, a.model,
                       a.llm_raw_response, a.error_message,
                       a.triggered_at, a.processing_started_at, a.completed_at
                FROM sessions s
                LEFT JOIN session_concentration_analysis a
                  ON a.session_id = s.id
                WHERE s.user_id = %s
                  AND s.ended_at IS NOT NULL
                ORDER BY s.ended_at DESC, s.started_at DESC
                LIMIT %s
                """,
                (user_id.lower(), limit),
            )

        rows = cursor.fetchall()
        items: list[dict[str, Any]] = []

        for row in rows:
            llm_parsed = _parse_json_object(row[8])
            item: dict[str, Any] = {
                "session_id": row[0],
                "session_key": row[1].lower() if isinstance(row[1], str) else None,
                "started_at": row[2],
                "ended_at": row[3],
                "status": row[4] or "pending",
                "score": row[5],
                "reason": row[6],
                "model": row[7],
                "error_message": row[9],
                "triggered_at": row[10],
                "processing_started_at": row[11],
                "completed_at": row[12],
            }

            if llm_parsed:
                item["phase_1"] = llm_parsed.get("phase_1")
                item["phase_2"] = llm_parsed.get("phase_2")
                item["phase_3"] = llm_parsed.get("phase_3")
                if item.get("score") is None:
                    item["score"] = llm_parsed.get("score")
                if not item.get("reason"):
                    item["reason"] = llm_parsed.get("reason")

            items.append(item)

        return {"items": items, "count": len(items)}
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_connection(connection)

