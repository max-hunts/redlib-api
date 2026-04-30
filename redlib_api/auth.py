"""
API key auth, rate limiting, usage logging, and key management.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite
import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = structlog.get_logger(__name__)

_MINUTE_LIMIT = 60
_DAY_LIMIT = 1_000

# Single persistent connection: one aiosqlite background thread for the process
# lifetime; pragmas and row_factory set once; aiosqlite's internal queue
# serialises all operations so no Python-level lock is needed.
_db: aiosqlite.Connection | None = None
_db_path: str = ""


def _resolve_db_path(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return database_url[len("sqlite:///") :]
    return database_url


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialized — call init_db() first.")
    return _db


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class KeyInfo(BaseModel):
    id: int
    name: str
    email: str | None = None
    is_active: bool
    created_at: str
    last_used: str | None = None


class UsageSummary(BaseModel):
    key_id: int
    requests_total: int
    requests_by_day: dict[str, int]


@dataclass
class AuthContext:
    key_id: int
    name: str
    minute_count: int
    day_count: int


# ---------------------------------------------------------------------------
# Custom exception — server.py registers the handler
# ---------------------------------------------------------------------------


class AuthError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.headers = headers or {}
        super().__init__(detail)


# ---------------------------------------------------------------------------
# RFC 7807 helper
# ---------------------------------------------------------------------------


def problem(status: int, title: str, detail: str, **extras: object) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
            **extras,
        },
    )


# ---------------------------------------------------------------------------
# DB lifecycle
# ---------------------------------------------------------------------------


def get_db() -> aiosqlite.Connection:
    """FastAPI dependency: returns the shared aiosqlite connection."""
    return _get_db()


async def init_db(database_url: str) -> None:
    """Open the persistent connection and create tables (idempotent)."""
    global _db, _db_path
    _db_path = _resolve_db_path(database_url)
    os.makedirs(os.path.dirname(os.path.abspath(_db_path)), exist_ok=True)
    _db = await aiosqlite.connect(_db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id          INTEGER PRIMARY KEY,
            key_hash    TEXT NOT NULL UNIQUE,
            name        TEXT NOT NULL,
            email       TEXT,
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            last_used   TEXT
        )
        """
    )
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_log (
            id          INTEGER PRIMARY KEY,
            key_id      INTEGER NOT NULL REFERENCES api_keys(id),
            ts          TEXT NOT NULL,
            endpoint    TEXT NOT NULL,
            status_code INTEGER NOT NULL
        )
        """
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_key_ts ON usage_log(key_id, ts)"
    )
    await _db.commit()
    logger.info("db_initialized", path=_db_path)


async def close_db() -> None:
    """Close the persistent connection. Call from the app lifespan shutdown."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# ---------------------------------------------------------------------------
# Key management helpers
# ---------------------------------------------------------------------------


async def create_key(name: str, email: str | None = None) -> str:
    """Store SHA-256 hash of a new UUID key; return the plaintext UUID once."""
    token = str(uuid.uuid4())
    key_hash = _hash_token(token)
    now = _utcnow().isoformat()
    db = _get_db()
    await db.execute(
        "INSERT INTO api_keys (key_hash, name, email, created_at) VALUES (?, ?, ?, ?)",
        (key_hash, name, email, now),
    )
    await db.commit()
    logger.info("key_created", name=name)
    return token


async def revoke_key(key_id: int) -> None:
    db = _get_db()
    await db.execute("UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,))
    await db.commit()
    logger.info("key_revoked", key_id=key_id)


async def list_keys() -> list[KeyInfo]:
    db = _get_db()
    cursor = await db.execute(
        "SELECT id, name, email, is_active, created_at, last_used FROM api_keys ORDER BY id"
    )
    rows = await cursor.fetchall()
    return [
        KeyInfo(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            last_used=row["last_used"],
        )
        for row in rows
    ]


async def get_usage(key_id: int, since: datetime) -> UsageSummary:
    db = _get_db()
    cursor = await db.execute(
        "SELECT substr(ts, 1, 10) AS day, COUNT(*) AS cnt "
        "FROM usage_log WHERE key_id = ? AND ts >= ? "
        "GROUP BY day ORDER BY day",
        (key_id, since.isoformat()),
    )
    rows = await cursor.fetchall()
    requests_by_day = {row["day"]: row["cnt"] for row in rows}
    return UsageSummary(
        key_id=key_id,
        requests_total=sum(requests_by_day.values()),
        requests_by_day=requests_by_day,
    )


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


async def require_api_key(request: Request) -> AuthContext:
    """FastAPI dependency: validate bearer token and enforce rate limits."""
    auth_header = request.headers.get("Authorization", "")
    _rl_stub = {
        "X-RateLimit-Limit": str(_MINUTE_LIMIT),
        "X-RateLimit-Remaining": str(_MINUTE_LIMIT),
        "X-RateLimit-Reset": str(int(_utcnow().timestamp()) + 60),
    }

    if not auth_header.startswith("Bearer "):
        raise AuthError(401, "Unauthorized", "Missing or malformed Authorization header", headers=_rl_stub)

    token = auth_header[len("Bearer ") :].strip()
    if not token:
        raise AuthError(401, "Unauthorized", "Bearer token is empty", headers=_rl_stub)

    key_hash = _hash_token(token)
    now = _utcnow()
    minute_ago = (now - timedelta(seconds=60)).isoformat()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    db = _get_db()
    cursor = await db.execute(
        "SELECT id, name, is_active FROM api_keys WHERE key_hash = ?",
        (key_hash,),
    )
    row = await cursor.fetchone()

    if row is None or not row["is_active"]:
        logger.warning("auth_failed", path=str(request.url.path))
        raise AuthError(401, "Unauthorized", "Invalid or revoked API key", headers=_rl_stub)

    key_id: int = row["id"]
    name: str = row["name"]

    m_cursor = await db.execute(
        "SELECT COUNT(*) FROM usage_log WHERE key_id = ? AND ts >= ?",
        (key_id, minute_ago),
    )
    m_row = await m_cursor.fetchone()
    minute_count: int = m_row[0] if m_row else 0

    d_cursor = await db.execute(
        "SELECT COUNT(*) FROM usage_log WHERE key_id = ? AND ts >= ?",
        (key_id, day_start),
    )
    d_row = await d_cursor.fetchone()
    day_count: int = d_row[0] if d_row else 0

    ctx = AuthContext(key_id=key_id, name=name, minute_count=minute_count, day_count=day_count)
    # Set before potentially raising 429 so the middleware can log the attempt.
    request.state.auth_context = ctx

    reset_ts = str(int(now.timestamp()) + 60)

    if minute_count >= _MINUTE_LIMIT:
        logger.warning(
            "rate_limit_exceeded", key_id=key_id, window="minute", path=str(request.url.path)
        )
        raise AuthError(
            429,
            "Too Many Requests",
            f"Minute rate limit exceeded ({_MINUTE_LIMIT} req/min)",
            headers={
                "Retry-After": "60",
                "X-RateLimit-Limit": str(_MINUTE_LIMIT),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": reset_ts,
            },
        )

    if day_count >= _DAY_LIMIT:
        seconds_to_midnight = int(
            (now.replace(hour=23, minute=59, second=59, microsecond=999999) - now).total_seconds()
            + 1
        )
        logger.warning(
            "rate_limit_exceeded", key_id=key_id, window="day", path=str(request.url.path)
        )
        raise AuthError(
            429,
            "Too Many Requests",
            f"Daily rate limit exceeded ({_DAY_LIMIT} req/day)",
            headers={
                "Retry-After": str(seconds_to_midnight),
                "X-RateLimit-Limit": str(_MINUTE_LIMIT),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": reset_ts,
            },
        )

    return ctx


# ---------------------------------------------------------------------------
# Usage log middleware
# ---------------------------------------------------------------------------


class UsageLogMiddleware(BaseHTTPMiddleware):
    """
    Post-response: inserts a usage_log row and updates last_used for every
    authenticated request. Also stamps X-RateLimit-* headers on all responses
    that carry an auth_context. Uses the minute window for the headers — the
    tighter bound clients should self-throttle against.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)

        ctx: AuthContext | None = getattr(request.state, "auth_context", None)
        if ctx is None:
            return response

        now = _utcnow()
        try:
            db = _get_db()
            await db.execute(
                "INSERT INTO usage_log (key_id, ts, endpoint, status_code) VALUES (?, ?, ?, ?)",
                (ctx.key_id, now.isoformat(), str(request.url.path), response.status_code),
            )
            await db.execute(
                "UPDATE api_keys SET last_used = ? WHERE id = ?",
                (now.isoformat(), ctx.key_id),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("usage_log_write_error", key_id=ctx.key_id, exc_info=exc)

        new_count = ctx.minute_count + 1
        reset_ts = str(int(now.timestamp()) + 60)
        response.headers["X-RateLimit-Limit"] = str(_MINUTE_LIMIT)
        response.headers["X-RateLimit-Remaining"] = str(max(0, _MINUTE_LIMIT - new_count))
        response.headers["X-RateLimit-Reset"] = reset_ts

        return response


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


async def prune_usage_log(older_than_days: int = 30) -> int:
    """Delete rows older than the cutoff. Designed to run once at startup."""
    cutoff = (_utcnow() - timedelta(days=older_than_days)).isoformat()
    db = _get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM usage_log WHERE ts < ?", (cutoff,)
    )
    row = await cursor.fetchone()
    count: int = row[0] if row else 0
    if count:
        await db.execute("DELETE FROM usage_log WHERE ts < ?", (cutoff,))
        await db.commit()
        logger.info("usage_log_pruned", deleted=count, cutoff=cutoff)
    return count
