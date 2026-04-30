# /docker

You are a DevOps engineer containerising this project. The service is a Python FastAPI app (`redlib_api/server.py`) that wraps `RedlibClient` and runs via `uvicorn`. Dependencies are in `pyproject.toml`, managed with `uv`. **Read `CLAUDE.md` first — it is authoritative.**

## Your job

Produce a minimal, production-appropriate Docker setup. Do not over-engineer.

### Architecture you are containerising

Two services, one compose file:

1. **`redlib`** — the silvenga Redlib fork, the only data backend. Always pin by digest:
   ```
   ghcr.io/silvenga/redlib@sha256:d76693a3efff4a04c5229ace2f8c4175eb0dc373f0fdad1172a3324cab00c55f
   ```
   Never reference `:latest` without an `@sha256:…` qualifier. Bumps are deliberate, separate changes.

2. **`api`** — this codebase. Talks to `redlib` over the internal compose network.

### Deliver

1. **`Dockerfile`** — `python:3.12-slim` base. Install with `uv pip install --system .` (the project is installed from `pyproject.toml`; there is no `requirements.txt`). Run with `uvicorn redlib_api.server:app --host $REDLIB_API_HOST --port $REDLIB_API_PORT`. Non-root user (create one and `USER` to it). No dev dependencies in the image. Add a `HEALTHCHECK` against `GET /health` on `REDLIB_API_PORT`.

2. **`docker-compose.yml`** — two services:
   - `redlib`: SHA-pinned image above, `restart: unless-stopped`. In dev, publish `8089:8080` so the host can reach it for `uvicorn --reload`. In a prod-only compose, leave it unpublished.
   - `api`: built from `./Dockerfile`. `depends_on: [redlib]`. Set `REDLIB_BASE_URL=http://redlib:8080` (service-network hostname, internal port). Other env vars (`REDLIB_API_HOST`, `REDLIB_API_PORT`, `DATABASE_URL`, `LOG_LEVEL`, `LOG_FILE`, `LOG_CONSOLE`, `PORTAL_SECRET_KEY`, `PORTAL_ADMIN_PASSWORD_HASH`) come from `.env` — do not hardcode. Mount a named volume at `/app/data` so the SQLite DB (`redlib.db`) and rotating log file (`redlib-api.log`) survive restarts. `restart: unless-stopped`.

3. **`.dockerignore`** — exclude `.venv`, `__pycache__`, `.git`, `.env`, `*.pyc`, `data/`, `.claude/`, `.pytest_cache`, `*.egg-info`.

4. **Brief usage block in `README.md`** — `docker compose up -d`, how to pass env vars (point at `.env.example`), how to bump the redlib digest (deliberate edit).

### Constraints

- The `redlib` sidecar **is** required — do not omit it. The API has no other backend.
- SQLite lives on a named volume mount; never bake `data/` into the image.
- TLS is not in the Python app. Never publish `5001` directly to the public internet — a reverse proxy (Caddy/nginx) handles TLS. The compose file may publish it on `127.0.0.1` only for local testing.
- Keep the image small. Multi-stage builds only if they meaningfully reduce size.
- Do not invent env var names. Confirm them by reading `redlib_api/server.py`, `redlib_api/auth.py`, `redlib_api/portal.py`, `redlib_api/logging_config.py`, and `.env.example` before hardcoding anything.

### Before writing files

Check what actually exists:
- `redlib_api/server.py` — confirm host/port env vars and `/health` path before hardcoding.
- `pyproject.toml` — read the install entry point (`redlib-api`) and runtime deps.
- `.env.example` — the canonical list of env vars.

If `server.py` does not yet exist (Phase 2 not done), note what the Dockerfile will need but do not invent module paths.
