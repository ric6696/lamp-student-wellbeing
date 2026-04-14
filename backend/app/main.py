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


def _outputs_ready_for_analyst(
    user_response_mtime: float,
    concentration_baseline_mtime: float | None,
    pre_session_baseline_mtime: float | None,
) -> tuple[bool, str]:
    if not _concentration_output_path.exists():
        return False, f"missing_concentration_output:{_concentration_output_path}"
    if not _pre_session_context_path.exists():
        return False, f"missing_pre_session_context:{_pre_session_context_path}"

    concentration_mtime = _concentration_output_path.stat().st_mtime
    pre_session_mtime = _pre_session_context_path.stat().st_mtime

    # Accept outputs that are close in time to the review submission (same-session window),
    # or outputs that changed after submission (worker finished later).
    same_session_window_seconds = 300

    concentration_close = abs(concentration_mtime - user_response_mtime) <= same_session_window_seconds
    pre_session_close = abs(pre_session_mtime - user_response_mtime) <= same_session_window_seconds

    concentration_updated = (
        concentration_baseline_mtime is not None and concentration_mtime > concentration_baseline_mtime
    )
    pre_session_updated = (
        pre_session_baseline_mtime is not None and pre_session_mtime > pre_session_baseline_mtime
    )

    if not (concentration_close or concentration_updated):
        return False, "stale_concentration_output"
    if not (pre_session_close or pre_session_updated):
        return False, "stale_pre_session_context"

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
            "SELECT session_id, status, score, reason, model, error_message, triggered_at, processing_started_at, completed_at "
            "FROM session_concentration_analysis WHERE session_id = %s",
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No concentration analysis found for this session")

        return {
            "session_id": row[0],
            "status": row[1],
            "score": row[2],
            "reason": row[3],
            "model": row[4],
            "error_message": row[5],
            "triggered_at": row[6],
            "processing_started_at": row[7],
            "completed_at": row[8],
        }
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_connection(connection)

