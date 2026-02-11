from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import get_cors_origins, settings
from .db import close_pool, get_connection, init_pool, release_connection
from .ingest import ingest_batch
from .models import Batch
from .routes.dashboard import router as dashboard_router

app = FastAPI(title="LAMP Ingestion API")
app.include_router(dashboard_router)


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


