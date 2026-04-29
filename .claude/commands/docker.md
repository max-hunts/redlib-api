# /docker

You are a DevOps engineer containerising this project. The service is a Python FastAPI app (`server.py`) that wraps `RedlibClient` and runs via `uvicorn`. Dependencies are in `requirements.txt`, managed with `uv`.

## Your job

Produce a minimal, production-appropriate Docker setup. Do not over-engineer — this is not enterprise software.

### Deliver

1. **`Dockerfile`** — use `python:3.12-slim`, install deps with `uv pip install --system`, run with `uvicorn`. Respect `REDLIB_API_HOST` and `REDLIB_API_PORT` env vars already defined in `server.py`. Non-root user. No dev dependencies in the image.

2. **`docker-compose.yml`** — single `app` service, env vars via `.env` file (do not hardcode values), expose the configured port, restart policy `unless-stopped`.

3. **`.dockerignore`** — exclude `.venv`, `__pycache__`, `.git`, `.env`, `*.pyc`.

4. **Brief usage block in README** — `docker compose up -d`, how to pass env vars.

### Constraints

- No Redis, no DB, no sidecar services unless explicitly asked. The in-memory cache in `server.py` is enough for now.
- Keep the image small. Multi-stage builds only if they meaningfully reduce size.
- `HEALTHCHECK` instruction pointing at `GET /health`.
- If `requirements.txt` does not yet exist, note what it will need but do not invent content.

### Before writing files

Check whether `server.py` exists and read it to confirm the actual host/port env var names and the `/health` endpoint path before hardcoding anything.
