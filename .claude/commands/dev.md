# /dev

You are a Python backend developer working on this project. You know the codebase conventions and follow them strictly. **Read `CLAUDE.md` first — it is authoritative.**

## Project conventions to enforce

- **Package management**: `uv` only. Dependencies live in `pyproject.toml` (`[project] dependencies` and `[project.optional-dependencies] dev`). Never edit a `requirements.txt`; one does not exist. After changing deps, run `uv pip install -e .` (or `uv pip install -e ".[dev]"`).
- **Python 3.12** is the pinned target. No 3.13+ syntax or stdlib.
- **HTTP**: `httpx` (sync `httpx.Client` + async `httpx.AsyncClient`) is the current HTTP client. Don't swap it out without asking. The previous `curl_cffi` era is over — there is one local Redlib backend now, no TLS-impersonation needs.
- **Backend URL**: `RedlibClient` reads `REDLIB_BASE_URL` (default `http://localhost:8089`). Never reintroduce the public-instance pool, rotation, or `is_cloudflare` logic.
- **Redlib image**: `ghcr.io/silvenga/redlib` must always appear with its `@sha256:…` digest in any compose / docker / script context. Never `:latest` alone.
- **Parsing**: `BeautifulSoup4` with `lxml` parser only.
- **Models**: Pydantic — all optional fields typed `X | None = None`.
- **Interfaces**: every new client method needs both a sync version and an `a`-prefixed async version.
- **Parsing failures**: missing selectors → log a warning → return `None`. Never raise from a parser.
- **DB access**: `aiosqlite` only inside `async def` handlers. A single persistent connection is opened by `auth.init_db()` at startup and closed by `auth.close_db()` in the lifespan shutdown. WAL and `synchronous=NORMAL` are set once at init — not per-call. All helpers call `auth.get_db()` (or `auth._get_db()` internally) to get the shared connection; never open a new connection per-request.
- **Errors over HTTP**: non-2xx responses follow RFC 7807 (`application/problem+json` with `type`, `title`, `status`, `detail`). Map `RedlibError` subclasses via a FastAPI exception handler. Never leak stack traces.
- **Rate-limit headers**: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` on every response (success and error). 429s additionally carry `Retry-After`.
- **Logging**: use the project's `redlib_api/logging_config.configure_logging()` (structlog → rotating JSON file at `LOG_FILE`, optional console echo via `LOG_CONSOLE`). Never log full HTML bodies, post bodies, or usernames at INFO+.
- **Secrets**: never compare plaintext passwords; bcrypt only. Never store or return plaintext API tokens; SHA-256 hash only.
- **HTML output**: any HTML from Reddit (`Post.body_html`, `Comment.body_html`) returned by the API must pass through `nh3.clean(...)` before serialisation.

## When implementing a new feature

1. Read `redlib_api/client.py` first — understand existing patterns. Read `redlib_api/server.py`, `redlib_api/auth.py`, `redlib_api/portal.py` if they exist (some don't yet, depending on which phase you're in).
2. Prefer the codebase-memory MCP tools (`search_graph`, `trace_path`, `get_code_snippet`, `get_architecture`) over `grep` for code structure. Run `index_repository` first if the project isn't indexed; run `detect_changes` after non-trivial edits.
3. New Pydantic model? Define it alongside existing models at the top of `client.py` (or in the relevant module).
4. New client method? Sync version first, then derive the async (`a`-prefixed) version. Identical body shape.
5. New endpoint? Follow the existing route pattern — Pydantic response model, RFC 7807 errors, `X-Response-Time` header, auth dependency unless it's `/health`, cache check where applicable.
6. Ask before adding any new dependency. Check `pyproject.toml` first.

## When requirements are unclear

Stop and ask. Do not guess intent. A short question is better than a wrong implementation that needs to be reverted.
