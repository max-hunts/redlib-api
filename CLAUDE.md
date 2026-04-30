# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**redlib-api** (RaaDS — Reddit as a Data Source): a Python service that runs a local [Redlib](https://github.com/redlib-org/redlib) container (the [silvenga fork](https://github.com/silvenga/redlib)) as its backend, parses Redlib's server-rendered HTML, and exposes data as clean JSON — as both an importable module and a standalone FastAPI server.

```
Your code → RedlibClient → Local Redlib container (silvenga fork) → Reddit (via spoofed Android OAuth)
```

Redlib serves no JSON endpoints. Everything is parsed from HTML. Do NOT use Reddit's API directly.

**Backend image (pinned by digest — do NOT float `:latest`):**

```
ghcr.io/silvenga/redlib@sha256:d76693a3efff4a04c5229ace2f8c4175eb0dc373f0fdad1172a3324cab00c55f
```

Bumping the digest is a deliberate, separate change. The historical public Redlib instance pool is dead (Anubis JS proof-of-work + Cloudflare on every reachable instance) and is no longer used.

## File Structure

```
redlib_api/
  __init__.py          # Re-exports RedlibClient + models
  client.py            # Core client class + Pydantic models + HTML parsers
  logging_config.py    # configure_logging() — structlog → rotating JSON file + optional console
  server.py            # FastAPI wrapper around RedlibClient
  auth.py              # API key DB (SQLite), rate limiting, UsageLogMiddleware, key helpers
  validators.py        # Pure input validation (subreddit, username, sort, etc.) — no I/O
  sanitize.py          # clean_html() via nh3 — strips unsafe tags from body_html fields
  portal.py            # Minimal admin portal (FastAPI + Jinja2) for key management
templates/
  portal/
    index.html     # Key list + create/revoke UI
data/
  redlib.db        # SQLite database (gitignored)
  redlib-api.log   # Rotating JSON log file (gitignored)
pyproject.toml     # Project metadata + dependencies (use uv to install)
.env.example       # Documents all required env vars with safe placeholders
Dockerfile         # Production container image
docker-compose.yml # Local dev / deployment compose config
```

## Commands

```bash
uv pip install -e .

# Start the local Redlib backend (required before the API can serve real data)
docker compose up -d redlib
# or, ad-hoc:
docker run -d --rm -p 8089:8080 \
  ghcr.io/silvenga/redlib@sha256:d76693a3efff4a04c5229ace2f8c4175eb0dc373f0fdad1172a3324cab00c55f

# Run the API server
uvicorn redlib_api.server:app --reload
# or
python -m redlib_api.server

# Full Docker stack (api + redlib)
docker build -t redlib-api .
docker compose up

# Environment variables
REDLIB_API_HOST=0.0.0.0
REDLIB_API_PORT=5001
REDLIB_BASE_URL=http://localhost:8089       # local Redlib container (host: 8089 → container: 8080)
DATABASE_URL=sqlite:///./data/redlib.db
LOG_LEVEL=INFO                              # DEBUG | INFO | WARNING | ERROR
LOG_FILE=data/redlib-api.log               # rotating JSON log (10 MB × 5); relative to CWD
LOG_CONSOLE=false                          # set true in dev to also echo to stderr
PORTAL_SECRET_KEY=changeme                  # signs portal session cookie (use a long random string)
PORTAL_ADMIN_PASSWORD_HASH=<bcrypt hash>    # generate with: python -c "from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword'))"
```

When the API runs inside `docker compose`, set `REDLIB_BASE_URL=http://redlib:8080` (service-to-service hostname). When the API runs on the host (e.g. `uvicorn --reload`), use `http://localhost:8089` to reach the published port.

Use `uv` for all package resolution and installation — not `pip` directly. **Python 3.12 is the pinned target version** (matches the Docker base); do not use syntax or stdlib features newer than 3.12. No test suite exists yet. When adding tests, use `pytest`.

**Secrets / config loading.** Local development reads from a `.env` file at the project root via `python-dotenv` (loaded once at server startup before any other imports that might need env vars). In Docker, the same variables are injected by `docker-compose.yml` and `.env` is not used. The `.env` file is gitignored; a committed `.env.example` documents every required variable with safe placeholder values.

## Deployment

The service runs as a Docker container behind a reverse proxy (nginx or Caddy) that handles TLS termination. The app itself speaks plain HTTP on port 5001 — HTTPS is not wired into the Python code. Never expose port 5001 directly in production; always front it with the proxy.

The Docker image should use a `python:3.12-slim` base, run as a non-root user, and expose port 5001. The `data/` directory holding the SQLite file must be mounted as a named volume so it survives container restarts and rebuilds.

`docker-compose.yml` defines two services: `api` (this codebase) and `redlib` (the silvenga Redlib fork, pinned by SHA digest). The `api` service `depends_on: [redlib]` and reaches the backend at `http://redlib:8080`. Only the `api` port is published; `redlib` is reachable only on the internal compose network (and, in dev, on host port 8089).

## Working Style

Always ask clarifying questions before proceeding when requirements are ambiguous — especially in plan mode. Don't make assumptions about intent; prefer a short question over a wrong implementation.

When you complete a task, check the implementation plan below and tick off the relevant item(s). Only update the plan — don't add prose or notes unless a decision meaningfully changes the architecture.

**Use the codebase-memory MCP for code exploration.** Before grepping or reading files to understand structure, prefer the codebase-memory MCP tools (`search_graph`, `trace_path`, `get_code_snippet`, `query_graph`, `get_architecture`). If the project is not indexed yet, run `index_repository` first; after non-trivial changes (new files, renamed symbols, refactors), run `detect_changes` or re-index so the graph stays current. Fall back to `Grep`/`Read` only for non-code text, configs, or when the graph clearly lacks the answer.

## Implementation Plan

**Phase 0 — Core client (`redlib_api/client.py`)**
- [x] `RedlibClient` class with sync + async methods (`get_subreddit`, `get_post`, `get_comments`, `search`, `get_user`, `check_health`)
- [x] Single-base-URL HTTP client driven by `REDLIB_BASE_URL` (default `http://localhost:8089`); no instance pool, no rotation
- [x] Single rate limiter (default 10 req/s = 0.1 s gap, configurable)
- [x] `httpx` client (sync + async) with browser User-Agent and 10 s timeout
- [x] BeautifulSoup + lxml HTML parsers for each endpoint shape
- [x] Pydantic models (`Post`, `Comment`, `SubredditInfo`, `SearchResult`, `UserProfile`)
- [x] Exception hierarchy (`RedlibError` → `RedlibConnectionError` / `RedlibParseError` / `RedlibRateLimitError`)
- [x] Defensive parsing: missing selectors log warning + return `None`, never raise
- [x] **[tooling]** Set up `uv audit` as a pre-commit hook: add `pre-commit` to `[project.optional-dependencies] dev`; create `.pre-commit-config.yaml` with a local hook that runs `uv audit --frozen` (reads `uv.lock` natively, audits all 76 packages including dev deps); run `uv pip install -e ".[dev]" && pre-commit install`

**Phase 1 — Auth core (`auth.py`)**
- [x] **[security]** Validate `subreddit`, `username`, `sort`, and `time_filter` inputs in `server.py` before passing to `RedlibClient` — reject values containing `/`, `?`, `#`, or any character outside `[A-Za-z0-9_\-]`; allowlist `sort` against `{"hot","new","top","rising","controversial"}` and `time_filter` against `{"hour","day","week","month","year","all"}`
- [x] **[security]** Sanitise `Post.body_html` and `Comment.body_html` before returning from API endpoints — add `nh3` to dependencies and run `nh3.clean(html)` on both fields in `server.py` response serialisation (or in the parser); document that `body_html` is sanitised HTML, not raw Reddit markup
- [x] DB init: create tables, enable WAL + `synchronous=NORMAL`
- [x] `create_key`, `revoke_key`, `list_keys`, `get_usage` helpers
- [x] FastAPI dependency: extract bearer → SHA-256 lookup → rate limit checks → 401/429
- [x] `usage_log` insert after response + `last_used` update
- [x] `X-RateLimit-*` headers on all responses
- [x] 30-day pruning task (run on startup)

**Phase 2 — Server integration (`server.py`)**
- [x] Wire auth dependency onto all routes except `/health`
- [x] `/health` probes the local Redlib backend → `{"status": "ok" | "degraded", "redlib": {"base_url": ..., "ok": bool}}`
- [x] Confirm `X-Response-Time` + `X-RateLimit-*` coexist cleanly

**Phase 3 — Admin portal (`portal.py` + templates)**
- [x] bcrypt login form + session cookie
- [x] Key list with per-key daily usage summary
- [x] Create key (display UUID once)
- [x] Revoke key
- [x] Key detail: usage by day, last 7 days

**Phase 4 — Container**
- [x] `Dockerfile`: `python:3.12-slim`, non-root user, port 5001
- [x] `docker-compose.yml`: `api` + `redlib` services; SHA-pinned `ghcr.io/silvenga/redlib`; `api` `depends_on: [redlib]`; named volume for `data/`; env var placeholders

## Architecture

### RedlibClient (`redlib_api/client.py`)

The central class. Provides both sync and async interfaces:

```python
client = RedlibClient(base_url=None, rate_limit=10.0, timeout=10.0)
# base_url defaults to os.environ["REDLIB_BASE_URL"] or "http://localhost:8089"

# Sync
client.get_subreddit("python", sort="hot", limit=25, after=None)
client.get_post("/r/python/comments/abc123/slug/")
client.get_comments("/r/python/comments/abc123/slug/")
client.search("query", subreddit="python")
client.get_user("username")
client.check_health()          # -> {"base_url": str, "ok": bool}
client.base_url                # property

# Async (same methods prefixed with `a`)
await client.aget_subreddit(...)
```

**Backend**: a single locally-running [silvenga/redlib](https://github.com/silvenga/redlib) container, pinned by SHA digest (see top-of-file). No instance pool, no rotation, no Cloudflare-aware ordering — those existed for the public-instance era and were removed when every public instance broke.

**Rate limiting**: minimum 0.1 s between requests to the backend (10 req/s; configurable via `rate_limit`). Local Redlib still hits Reddit upstream, so some throttle is still polite — but the public-instance 1 req/s cap no longer applies.

**HTTP client**: `httpx` (sync `httpx.Client` + async `httpx.AsyncClient`) with a realistic browser User-Agent and 10 s timeout (configurable).

### HTML Parsing

Use `BeautifulSoup4` with the `lxml` parser.

Redlib URL patterns:

- Listing: `/r/{sub}` or `/r/{sub}/{sort}` — sort: `hot|new|top|rising|controversial`
- Top/controversial: add `?t=hour|day|week|month|year|all`
- Post+comments: `/r/{sub}/comments/{id}/{slug}/`
- Search: `/search?q={query}` or with `&restrict_sr=on&sub={sub}`
- User: `/user/{username}`
- Pagination: `?after={token}`

**Critical parsing rule**: if any selector returns nothing, log a warning and return `None` for that field — never crash. Redlib HTML can change between versions and not all fields appear on all posts.

Key HTML classes (from Redlib's Askama templates):

- Post container: `div.post`
- Post title: `a.post_title`
- Score: `div.post_score`
- Author: `a.post_author`
- Subreddit: `a.post_subreddit`
- Comment count: `a.post_comments`
- Timestamp: `span.post_time`
- Flair: `span.post_flair`
- Thumbnail: `img.post_thumbnail`
- NSFW marker: check for `.nsfw` class or `nsfw` in class list
- Pagination next/prev: `a` links containing `after=`/`before=` in href
- Comment container: `div.comment`
- Comment body: `div.comment_body`
- Sidebar (subreddit info): `div#sidebar`

### Pydantic Models

```python
Post(id, title, author, subreddit, score, url, external_url, body_html,
     body_text, comment_count, created, flair, nsfw, thumbnail_url, media_urls)

Comment(id, author, score, body_html, body_text, created, depth, replies)

SubredditInfo(name, title, description, members, active)

SearchResult(posts, next_page_token)

UserProfile(username, karma, created, posts, comments)
```

All fields that might be absent are typed as `X | None` with a default of `None`.

### Exceptions

```
RedlibError             # base
├── RedlibConnectionError   # local Redlib unreachable / 5xx / timeout
├── RedlibParseError        # unexpected HTML structure
└── RedlibRateLimitError    # backend returned 429
```

### FastAPI Server (`server.py`)

Thin wrapper around `RedlibClient`. All endpoints return Pydantic model JSON.

```
GET /r/{subreddit}?sort=hot&limit=25&after=xxx
GET /r/{subreddit}/comments/{post_id}/{slug}
GET /search?q=query&subreddit=python
GET /user/{username}
GET /health
```

- CORS enabled (configurable origins, default `*`)
- `X-Response-Time` header on all responses
- Host/port via `REDLIB_API_HOST` / `REDLIB_API_PORT` env vars
- All routes except `/health` require a valid bearer token (via `auth.py` dependency)
- `GET /health` probes the local Redlib backend and returns `{"status": "ok" | "degraded", "redlib": {"base_url": <url>, "ok": <bool>}}`. `status` is `degraded` when the backend probe fails; the API itself still responds 200 so external monitors can distinguish API-down from backend-down.
- The admin portal (`portal.py`) is mounted into this same app via `app.mount("/portal", portal_app)` — single process, single `uvicorn server:app` invocation runs both.

**Caching.** Optional in-memory cache with 60s default TTL.
- **Key:** the full upstream Redlib URL (path + query string, normalised) — *not* per-API-key. Cache is shared across authenticated callers since the upstream response is identical.
- **Auth still enforced first.** The auth dependency runs before the cache lookup; an unauthenticated or rate-limited request never reads or writes the cache.
- **Size cap: 1 GiB total.** Track approximate byte size of cached response bodies. On insert, if adding the new entry would exceed 1 GiB, evict by LRU until it fits. If a single entry exceeds the cap, skip caching it (do not crash).
- **Overflow handling:** never raise out of the cache layer — log a warning at WARNING level and serve the upstream response uncached. The server must keep running even if the cache is misbehaving.

**Error responses.** Follow [RFC 7807 Problem Details for HTTP APIs](https://datatracker.ietf.org/doc/html/rfc7807). All non-2xx responses return `application/problem+json` with at minimum `type`, `title`, `status`, and `detail` fields. Status mapping:
- `401` — missing/invalid bearer token
- `429` — rate limit exceeded (with `Retry-After` + `X-RateLimit-*`)
- `502` — upstream Redlib returned malformed HTML / parse error
- `503` — local Redlib backend unreachable (`RedlibConnectionError`); include `Retry-After` if known
- `504` — upstream timeout

A FastAPI exception handler maps `RedlibError` subclasses to the right status + problem+json body; never let a raw stack trace leak.

### Auth & API Keys (`auth.py`)

SQLite-backed API key system. Keys are UUID v4 tokens, stored hashed (SHA-256); the plaintext UUID is shown to the admin once at creation time and never stored.

**Database schema:**

```sql
CREATE TABLE api_keys (
    id          INTEGER PRIMARY KEY,
    key_hash    TEXT NOT NULL UNIQUE,   -- SHA-256 of the UUID token
    name        TEXT NOT NULL,          -- human label (e.g. "acme corp")
    email       TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,          -- ISO-8601 UTC
    last_used   TEXT                    -- ISO-8601 UTC, updated on each request
);

CREATE TABLE usage_log (
    id          INTEGER PRIMARY KEY,
    key_id      INTEGER NOT NULL REFERENCES api_keys(id),
    ts          TEXT NOT NULL,          -- ISO-8601 UTC timestamp
    endpoint    TEXT NOT NULL,
    status_code INTEGER NOT NULL
);

CREATE INDEX idx_usage_key_ts ON usage_log(key_id, ts);
```

**Usage log retention:** rows older than 30 days serve no purpose (rate limit windows are at most 24 h). A cleanup routine should purge them — either a background task on startup or a periodic call. Without this, `COUNT` queries for rate limiting degrade linearly with table size.

**SQLite concurrency:** FastAPI runs an async event loop; blocking SQLite calls inside `async def` handlers will stall it under concurrent load. Use `aiosqlite` for all DB access so queries run without blocking the loop.

A **single persistent `aiosqlite.Connection`** is opened by `init_db()` at startup and closed by `close_db()` in the lifespan shutdown hook. WAL and `synchronous=NORMAL` are set once at `init_db` time — not per-call. `get_db()` is a plain sync function that returns this shared connection; all callers use it directly. aiosqlite's internal background thread and queue serialise every operation, so no Python-level lock is needed.

WAL allows concurrent readers alongside a single writer, which fits the read-heavy access pattern here (many rate-limit checks, occasional inserts). Without it, any write locks out all reads, causing request queuing.

**Basic tier limits (single tier):**

- 60 requests / minute
- 1 000 requests / day

**DB lifecycle:**

```python
await init_db(database_url)   # call once in lifespan startup — opens connection, sets pragmas, creates schema
await close_db()              # call once in lifespan shutdown
get_db() -> Connection        # sync FastAPI dependency; returns the shared connection
```

**Enforcement — `require_api_key` dependency:**

1. Extract `Authorization: Bearer <token>` header — raise `AuthError(401)` if missing.
2. SHA-256 the token, look up in `api_keys` — `AuthError(401)` if not found or `is_active = 0`.
3. Count `usage_log` rows for this key in the last 60 s — `AuthError(429)` if ≥ 60.
4. Count `usage_log` rows for this key since UTC midnight — `AuthError(429)` if ≥ 1 000.
5. Store `AuthContext(key_id, name, minute_count, day_count)` in `request.state.auth_context` **before** any 429 raise, so the middleware can log rate-limited attempts.
6. Return `AuthContext` to the route handler.

**Post-response — `UsageLogMiddleware`:**

Reads `request.state.auth_context`; if present:
- Inserts one row into `usage_log` (key_id, ts, endpoint, status_code).
- Updates `last_used` on the key row.
- Sets `X-RateLimit-Limit / Remaining / Reset` headers on the response (minute window — the tighter client-visible bound).

The middleware catches its own DB exceptions and logs a WARNING rather than letting them surface to the caller.

**`AuthError` and `problem()`:**

`AuthError(status, title, detail, headers)` is raised by the dependency; `server.py` registers an exception handler that calls `problem()` to produce an `application/problem+json` response. `problem(status, title, detail, **extras)` is a standalone helper that returns a `JSONResponse` with `media_type="application/problem+json"`.

429 responses carry `Retry-After` + the full `X-RateLimit-*` triple in `AuthError.headers`; the exception handler forwards them onto the response.

**Key management helpers (used by portal):**

```python
create_key(name, email) -> str          # generates UUID v4, stores hash, returns plaintext once
revoke_key(key_id)
list_keys() -> list[KeyInfo]
get_usage(key_id, since) -> UsageSummary
prune_usage_log(older_than_days=30) -> int  # call at startup; deletes stale rows
```

### Input Validation (`validators.py`)

Pure functions — no I/O, no FastAPI imports. Phase 2 route handlers call these before passing values to `RedlibClient`; on failure they raise `ValueError` with a short caller-safe message. Phase 2 catches and translates to `400 application/problem+json`.

```python
SUBREDDIT_RE   = r"^[A-Za-z0-9_\-]{1,50}$"
USERNAME_RE    = r"^[A-Za-z0-9_\-]{1,50}$"
POST_ID_RE     = r"^[A-Za-z0-9]{1,16}$"
SLUG_RE        = r"^[A-Za-z0-9_\-]{0,128}$"
AFTER_TOKEN_RE = r"^[A-Za-z0-9_\-]{0,64}$"

SORTS        = {"hot", "new", "top", "rising", "controversial"}
TIME_FILTERS = {"hour", "day", "week", "month", "year", "all"}

validate_subreddit(value) -> str
validate_username(value) -> str
validate_post_id(value) -> str
validate_slug(value) -> str
validate_sort(value) -> str
validate_time_filter(value) -> str
validate_after(value | None) -> str | None
validate_query(q, max_len=256) -> str
```

### HTML Sanitisation (`sanitize.py`)

```python
clean_html(html: str | None) -> str | None
```

Runs `nh3.clean()` with a tight allowlist (`p`, `br`, `strong`, `em`, `ul`, `ol`, `li`, `blockquote`, `code`, `pre`, `a`, `h1`–`h6`; only `href`/`title` on `<a>`). Phase 2 calls this on `Post.body_html` and `Comment.body_html` before serialisation. The returned value is safe HTML — consumers must not sanitise it again.

### Admin Portal (`portal.py`)

Minimal FastAPI app mounted at `/portal` on the same process. Uses Jinja2 templates and a session cookie for access. Protected by a single admin password stored as a **bcrypt hash** in the `PORTAL_ADMIN_PASSWORD_HASH` env var — never compared as plaintext. Use `passlib[bcrypt]` to hash on first setup and verify on login.

**Routes:**

```
GET  /portal/login              # login form
POST /portal/login              # verifies bcrypt hash, sets signed session cookie
GET  /portal/                   # key list with per-key usage summary
POST /portal/keys               # create new key (shows UUID plaintext once, then gone)
POST /portal/keys/{id}/revoke   # sets is_active = 0
GET  /portal/keys/{id}          # usage detail: requests by day for last 7 days
```

The portal is a functional skeleton. It will grow into a self-serve signup and billing flow later, but for now all key management is admin-only.

## Key Constraints

- **No Reddit API calls.** The silvenga Redlib fork handles OAuth spoofing internally; we only speak to it.
- **Pin the Redlib image by SHA digest.** Never reference `ghcr.io/silvenga/redlib:latest` in `docker-compose.yml`, `Dockerfile`, or scripts without the `@sha256:…` qualifier. Bumps are deliberate, separate changes.
- **Defensive parsing only.** Missing selectors → `None`, not exceptions.
- **Throttle the local backend.** Default 10 req/s rate limit — the local container is fast, but Reddit upstream isn't.
- **No JS.** Redlib is entirely server-rendered HTML.
- **Auth on all data endpoints.** `/health` is the only unauthenticated route on the main API.
- **Never store or return plaintext tokens.** Store only the SHA-256 hash; show the UUID once at creation time.
- **Never compare passwords as plaintext.** Portal admin password must be bcrypt-hashed.
- **Rate limit headers always present.** Include `X-RateLimit-*` on all responses (success and error) so clients can self-throttle.
- **HTTPS at the proxy.** The app speaks plain HTTP; TLS is the reverse proxy's responsibility. Never expose port 5001 directly.
- **Prune `usage_log` regularly.** Rows older than 30 days must be deleted to keep rate-limit queries fast.
- **Validate all route inputs.** Every subreddit name, username, post ID, slug, sort, time_filter, after token, and search query must pass through `validators.py` before reaching `RedlibClient`. Raise `ValueError`; Phase 2 translates to `400 application/problem+json`.
- **Sanitise all HTML output.** `Post.body_html` and `Comment.body_html` must pass through `sanitize.clean_html()` before being returned by any API endpoint. Raw Redlib HTML is never returned directly.
