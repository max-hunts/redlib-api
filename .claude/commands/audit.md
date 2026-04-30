# /audit

You are a code auditor reviewing this project for safety, security, and reliability issues. This is not an enterprise app, but it runs as a public-facing HTTP service that proxies a local Redlib backend, so some things matter. **Read `CLAUDE.md` first — it is authoritative.**

## Architecture context (so you don't waste time)

- One **local** Redlib container (silvenga fork, SHA-pinned) is the only backend. There is no public instance pool any more, no rotation, no `instances` parameter, no Cloudflare handling. `RedlibClient.base_url` is read from `REDLIB_BASE_URL`.
- Dependencies live in `pyproject.toml` and are locked in `uv.lock` — there is no `requirements.txt`. `pip-audit` should be wired into a pre-commit hook (Phase 0 tooling task).
- Auth is API-key-based (UUID v4, SHA-256 hashed at rest) with a SQLite-backed rate limiter via `aiosqlite` + WAL. Admin portal uses bcrypt + signed session cookie.

## What to check

### Input handling (server boundary)
- `subreddit`, `username`, `post_id`, `slug`, `sort`, `time_filter`, `q` come from API callers and end up in URL paths/queries sent to the local Redlib backend. Verify each is validated:
  - `subreddit`, `username`: regex `^[A-Za-z0-9_\-]{1,50}$`. Reject `/`, `?`, `#`, `..`, etc.
  - `sort`: allowlist `{"hot","new","top","rising","controversial"}`.
  - `time_filter`: allowlist `{"hour","day","week","month","year","all"}`.
  - `post_id`: regex `^[A-Za-z0-9]{1,16}$`.
  - `q`: length-bounded; URL-encoded by `httpx`, but verify nobody concatenates it into a path.
- `after` pagination tokens: bounded length, alphanumeric + `_-`, otherwise rejected.

### SSRF risk
- `REDLIB_BASE_URL` is operator-supplied via env, **not** caller-supplied. Confirm there is no endpoint that accepts an instance/base URL from the request body or query string. If one exists, that's high-severity SSRF.
- Confirm `RedlibClient.__init__` does not accept caller-controlled URLs in any HTTP-exposed path.

### Parsed content (XSS via API)
- `Post.body_html` and `Comment.body_html` are raw Redlib-rendered HTML. The API must run them through `nh3.clean(...)` before returning. If a frontend renders unsanitised `body_html`, that's stored XSS. Verify sanitisation happens in `server.py` response serialisation or in the parser itself, and that the contract is documented.

### Error and exception handling
- `RedlibConnectionError` → 503, `RedlibParseError` → 502, `RedlibRateLimitError` → 429, `httpx.TimeoutException` → 504, missing/bad token → 401. All as RFC 7807 `application/problem+json`. Verify a FastAPI exception handler maps them and that no raw stack trace, internal path, or backend URL leaks in the body or headers in production.

### Auth & rate limiting
- API keys: stored only as SHA-256 hash; plaintext shown once at creation. No plaintext anywhere on disk or in logs.
- Bearer comparison: should be a hash lookup, not a string compare on plaintext.
- Rate-limit counters: per-key, last-60-seconds and since-UTC-midnight. Confirm the SQL uses an indexed scan (`idx_usage_key_ts`) and that `usage_log` is pruned (>30 days) on startup or periodically.
- Admin portal: bcrypt only; never compares plaintext. Session cookie is signed with `PORTAL_SECRET_KEY`; cookie should be `HttpOnly`, `Secure` (when behind TLS proxy), `SameSite=Lax` minimum.

### Concurrency
- All DB access in `async def` handlers must use `aiosqlite`. A blocking `sqlite3` call inside an async handler is a real bug here — flag it.
- Every connection opens with `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL`.
- The `_RateLimiter` uses an `asyncio.Lock` for the async path. Confirm the sync path is not called from an async context (it would block the loop).

### Logging
- Verify INFO-level logs do not include: full HTML bodies, post/comment text, usernames, API tokens (even hashed prefixes are risky in shared logs), bearer headers, query strings of `q=`. DEBUG may include more, but the default is INFO and the rotating file at `LOG_FILE` is not user-isolated.

### Dependencies
- Scan `pyproject.toml` (and `uv.lock` if present) for obviously outdated or unmaintained packages. Note packages with known CVEs only if you genuinely recognise them — never fabricate CVE numbers. Flag if `pip-audit` is not yet wired into the pre-commit hook (Phase 0 tooling checklist item).

### Container hygiene
- `ghcr.io/silvenga/redlib` referenced anywhere without `@sha256:…` is a finding. Bumping must be deliberate.
- API container must run as non-root, not expose port 5001 to the host directly in production (proxy fronts it), and mount `data/` as a named volume.

## How to report

For each issue: **severity** (low/medium/high), **location** (file + line if possible), **what the problem is**, and a **concrete fix**. Skip anything acceptable for a non-enterprise project of this scope. Don't pad.
