import logging
import time
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import get_cors_origins, settings
from .db import close_pool, get_connection, init_pool, release_connection
from .ingest import ingest_batch
from .models import Batch

app = FastAPI(title="LAMP Ingestion API")

_repo_root = Path(__file__).resolve().parents[2]
_log_dir = _repo_root / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

http_logger = logging.getLogger("ingest.http")
if not http_logger.handlers:
    handler = logging.FileHandler(_log_dir / "ingest_http.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    http_logger.addHandler(handler)
    http_logger.setLevel(logging.INFO)


@app.on_event("startup")
def startup() -> None:
    init_pool()


@app.on_event("shutdown")
def shutdown() -> None:
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


@app.post("/ingest")
async def ingest(payload: Batch, background_tasks: BackgroundTasks, _: None = Depends(require_api_key)):
    background_tasks.add_task(ingest_batch, payload)
    return {"status": "accepted", "records": len(payload.data)}


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


