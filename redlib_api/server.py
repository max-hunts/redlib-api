"""FastAPI server: auth, caching, validation, sanitization wired around RedlibClient."""

from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from redlib_api.auth import (
    AuthContext,
    AuthError,
    UsageLogMiddleware,
    close_db,
    init_db,
    problem,
    prune_usage_log,
    require_api_key,
)
from redlib_api.client import (
    RedlibClient,
    RedlibConnectionError,
    RedlibParseError,
    RedlibRateLimitError,
)
from redlib_api.logging_config import configure_logging
from redlib_api.sanitize import clean_html
from redlib_api.validators import (
    validate_after,
    validate_post_id,
    validate_query,
    validate_slug,
    validate_sort,
    validate_subreddit,
    validate_time_filter,
    validate_username,
)

load_dotenv()

logger = structlog.get_logger(__name__)
_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/redlib.db")
_REDLIB_CLIENT: RedlibClient | None = None


# ---------------------------------------------------------------------------
# LRU cache (shared across authenticated callers, keyed by upstream URL)
# ---------------------------------------------------------------------------


class _CacheEntry:
    __slots__ = ("expires_at", "approx_bytes", "data")

    def __init__(self, expires_at: float, approx_bytes: int, data: Any) -> None:
        self.expires_at = expires_at
        self.approx_bytes = approx_bytes
        self.data = data


class _LRUCache:
    _MAX_BYTES = 1 * 1024**3  # 1 GiB

    def __init__(self, ttl: float = 60.0) -> None:
        self._ttl = ttl
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._total_bytes = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            self._total_bytes -= entry.approx_bytes
            return None
        self._store.move_to_end(key)
        return entry.data

    def set(self, key: str, data: Any, approx_bytes: int) -> None:
        if approx_bytes > self._MAX_BYTES:
            logger.warning("cache_entry_too_large", approx_bytes=approx_bytes)
            return
        existing = self._store.pop(key, None)
        if existing is not None:
            self._total_bytes -= existing.approx_bytes
        while self._total_bytes + approx_bytes > self._MAX_BYTES and self._store:
            _, evicted = self._store.popitem(last=False)
            self._total_bytes -= evicted.approx_bytes
        self._store[key] = _CacheEntry(
            expires_at=time.monotonic() + self._ttl,
            approx_bytes=approx_bytes,
            data=data,
        )
        self._total_bytes += approx_bytes


_cache = _LRUCache()


def _make_cache_key(path: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return path
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
    return f"{path}?{qs}" if qs else path


def _cache_get(key: str) -> Any | None:
    try:
        return _cache.get(key)
    except Exception as exc:
        logger.warning("cache_read_error", key=key, exc_info=exc)
        return None


def _cache_set(key: str, data: Any) -> None:
    try:
        approx_bytes = len(json.dumps(data).encode())
        _cache.set(key, data, approx_bytes)
    except Exception as exc:
        logger.warning("cache_write_error", key=key, exc_info=exc)


# ---------------------------------------------------------------------------
# HTML sanitization helpers
# ---------------------------------------------------------------------------


def _sanitize_post(post: dict[str, Any]) -> None:
    if post.get("body_html") is not None:
        post["body_html"] = clean_html(post["body_html"])


def _sanitize_comment(comment: dict[str, Any]) -> None:
    if comment.get("body_html") is not None:
        comment["body_html"] = clean_html(comment["body_html"])
    for reply in comment.get("replies", []):
        _sanitize_comment(reply)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _REDLIB_CLIENT
    configure_logging()
    await init_db(_DATABASE_URL)
    await prune_usage_log()
    _REDLIB_CLIENT = RedlibClient()
    logger.info("server_started")
    yield
    if _REDLIB_CLIENT is not None:
        await _REDLIB_CLIENT.aclose()
    await close_db()
    logger.info("server_stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="redlib-api", lifespan=_lifespan)

# Middleware is executed outermost-first on the request path and innermost-first
# on the response path. add_middleware() builds the stack in reverse, so the
# last call here becomes the outermost layer.
#
# Response path: Route → UsageLogMiddleware → _TimingMiddleware → CORSMiddleware
#
# UsageLogMiddleware stamps X-RateLimit-* headers closest to the route;
# _TimingMiddleware stamps X-Response-Time after those are set;
# CORSMiddleware wraps everything so it can intercept OPTIONS preflights.

app.add_middleware(UsageLogMiddleware)


class _TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        t0 = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - t0) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        return response


app.add_middleware(_TimingMiddleware)

_CORS_ORIGINS: list[str] = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(AuthError)
async def _auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    resp = problem(exc.status, exc.title, exc.detail)
    for k, v in exc.headers.items():
        resp.headers[k] = v
    return resp


@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return problem(400, "Bad Request", str(exc))


@app.exception_handler(RedlibConnectionError)
async def _connection_error_handler(
    request: Request, exc: RedlibConnectionError
) -> JSONResponse:
    logger.warning("redlib_connection_error", detail=str(exc))
    return problem(503, "Service Unavailable", "Local Redlib backend is unreachable")


@app.exception_handler(RedlibParseError)
async def _parse_error_handler(request: Request, exc: RedlibParseError) -> JSONResponse:
    logger.warning("redlib_parse_error", detail=str(exc))
    return problem(502, "Bad Gateway", "Redlib returned unexpected HTML")


@app.exception_handler(RedlibRateLimitError)
async def _backend_rate_limit_handler(
    request: Request, exc: RedlibRateLimitError
) -> JSONResponse:
    logger.warning("redlib_backend_rate_limit", detail=str(exc))
    return problem(502, "Bad Gateway", "Redlib backend is rate limiting requests")


# ---------------------------------------------------------------------------
# Client accessor
# ---------------------------------------------------------------------------


def _get_client() -> RedlibClient:
    if _REDLIB_CLIENT is None:
        raise RuntimeError("RedlibClient not initialized")
    return _REDLIB_CLIENT


# ---------------------------------------------------------------------------
# Routes (more specific paths defined first)
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> JSONResponse:
    if _REDLIB_CLIENT is None:
        return JSONResponse({"status": "degraded", "redlib": {"base_url": None, "ok": False}})
    result = await _REDLIB_CLIENT.acheck_health()
    status = "ok" if result["ok"] else "degraded"
    return JSONResponse({"status": status, "redlib": result})


@app.get("/r/{subreddit}/comments/{post_id}/{slug}")
async def get_comments(
    subreddit: str,
    post_id: str,
    slug: str,
    _auth: AuthContext = Depends(require_api_key),
) -> JSONResponse:
    sub = validate_subreddit(subreddit)
    pid = validate_post_id(post_id)
    slg = validate_slug(slug)

    path = f"/r/{sub}/comments/{pid}/{slg}/"
    cached = _cache_get(path)
    if cached is not None:
        return JSONResponse(cached)

    data = await _get_client().aget_comments(path)
    data = jsonable_encoder(data)
    if data.get("post"):
        _sanitize_post(data["post"])
    for comment in data.get("comments", []):
        _sanitize_comment(comment)
    _cache_set(path, data)
    return JSONResponse(data)


@app.get("/r/{subreddit}")
async def get_subreddit(
    subreddit: str,
    sort: str = "hot",
    limit: int = 25,
    after: str | None = None,
    time_filter: str | None = None,
    _auth: AuthContext = Depends(require_api_key),
) -> JSONResponse:
    sub = validate_subreddit(subreddit)
    srt = validate_sort(sort)
    aft = validate_after(after)
    tf = validate_time_filter(time_filter) if time_filter is not None else None

    cache_key = _make_cache_key(f"/r/{sub}/{srt}", {"limit": limit, "after": aft, "t": tf})
    cached = _cache_get(cache_key)
    if cached is not None:
        return JSONResponse(cached)

    data = await _get_client().aget_subreddit(sub, sort=srt, limit=limit, after=aft, time_filter=tf)
    data = jsonable_encoder(data)
    for post in data.get("posts", []):
        _sanitize_post(post)
    _cache_set(cache_key, data)
    return JSONResponse(data)


@app.get("/search")
async def search(
    q: str = Query(...),
    subreddit: str | None = Query(default=None),
    after: str | None = Query(default=None),
    _auth: AuthContext = Depends(require_api_key),
) -> JSONResponse:
    query = validate_query(q)
    sub = validate_subreddit(subreddit) if subreddit is not None else None
    aft = validate_after(after)

    cache_key = _make_cache_key("/search", {"q": query, "sub": sub, "after": aft})
    cached = _cache_get(cache_key)
    if cached is not None:
        return JSONResponse(cached)

    data = await _get_client().asearch(query, subreddit=sub, after=aft)
    data = jsonable_encoder(data)
    for post in data.get("posts", []):
        _sanitize_post(post)
    _cache_set(cache_key, data)
    return JSONResponse(data)


@app.get("/user/{username}")
async def get_user(
    username: str,
    _auth: AuthContext = Depends(require_api_key),
) -> JSONResponse:
    uname = validate_username(username)
    cache_key = _make_cache_key(f"/user/{uname}")
    cached = _cache_get(cache_key)
    if cached is not None:
        return JSONResponse(cached)

    data = await _get_client().aget_user(uname)
    data = jsonable_encoder(data)
    for post in data.get("posts", []):
        _sanitize_post(post)
    for comment in data.get("comments", []):
        _sanitize_comment(comment)
    _cache_set(cache_key, data)
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import uvicorn

    host = os.environ.get("REDLIB_API_HOST", "0.0.0.0")
    port = int(os.environ.get("REDLIB_API_PORT", "5001"))
    uvicorn.run("redlib_api.server:app", host=host, port=port)


if __name__ == "__main__":
    main()
