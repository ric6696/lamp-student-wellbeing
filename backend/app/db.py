import psycopg2
from typing import Optional
from psycopg2.pool import ThreadedConnectionPool

from .config import settings

_pool: Optional[ThreadedConnectionPool] = None

def init_pool() -> None:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            host=settings.postgres_host,
            port=settings.postgres_port,
        )

def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None

def get_connection():
    if _pool is None:
        init_pool()
    return _pool.getconn()

def release_connection(connection) -> None:
    if _pool is None:
        return
    _pool.putconn(connection)
