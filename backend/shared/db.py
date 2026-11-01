"""Postgres access: one pool, a transaction helper, and a startup migration runner (ADR 0005).

Plain SQL, rows come back as dicts. Call `init()` once (bootstrap does) before anything else.
"""
import os
from contextlib import contextmanager
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

DEV_URL = "postgresql://azentio:azentio@localhost:5432/azentio"
MIGRATIONS = Path(__file__).parent.parent / "migrations"

_pool: ConnectionPool | None = None
Json = Jsonb  # re-export so callers don't import psycopg types


def url() -> str:
    return os.getenv("DATABASE_URL") or DEV_URL


def init(dsn: str | None = None, wait: float = 20.0) -> None:
    """Open the pool, waiting up to `wait` seconds for Postgres. Raises on timeout."""
    global _pool
    if _pool is not None:
        return
    pool = ConnectionPool(dsn or url(), min_size=1, max_size=int(os.getenv("DB_POOL_MAX", "10")),
                          kwargs={"row_factory": dict_row, "autocommit": False}, open=False)
    pool.open(wait=True, timeout=wait)
    _pool = pool


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("db.init() has not run")
    return _pool


@contextmanager
def tx():
    """A connection inside one transaction: commits on success, rolls back on any exception."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


def fetch(sql: str, params: dict | tuple | None = None, conn=None) -> list[dict]:
    if conn is not None:
        return conn.execute(sql, params).fetchall()
    with tx() as c:
        return c.execute(sql, params).fetchall()


def fetch1(sql: str, params: dict | tuple | None = None, conn=None) -> dict | None:
    rows = fetch(sql, params, conn)
    return rows[0] if rows else None


def execute(sql: str, params: dict | tuple | None = None, conn=None) -> int:
    """Run a statement, return affected row count."""
    if conn is not None:
        return conn.execute(sql, params).rowcount
    with tx() as c:
        return c.execute(sql, params).rowcount


def ping() -> bool:
    try:
        return fetch1("SELECT 1 AS ok")["ok"] == 1
    except Exception:
        return False


def use_test_database(name: str = "azentio_test") -> str:
    """Self-checks only: recreate an empty database next to the configured one, point the pool at it, migrate."""
    import psycopg
    admin = url()
    test = admin.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {name}")
    close()
    init(test)
    migrate()
    os.environ["DATABASE_URL"] = test
    return test


def migrate() -> list[str]:
    """Apply backend/migrations/NNN_*.sql in order, each in its own transaction. Returns newly applied names."""
    with tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        # One migrator at a time, even with several app replicas starting together.
        c.execute("SELECT pg_advisory_xact_lock(7243001)")
    applied = []
    for f in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        with tx() as c:
            c.execute("SELECT pg_advisory_xact_lock(7243001)")
            if c.execute("SELECT 1 FROM schema_migrations WHERE name=%s", (f.name,)).fetchone():
                continue
            c.execute(f.read_text(encoding="utf-8"))
            c.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
            applied.append(f.name)
    return applied
