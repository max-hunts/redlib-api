# redlib-api
RaaDS - Reddit as a Data Source

## Running with Docker

Copy `.env.example` to `.env` and fill in the required values:

```bash
cp .env.example .env
# Edit .env — set PORTAL_SECRET_KEY and PORTAL_ADMIN_PASSWORD_HASH at minimum
```

Start both services:

```bash
docker compose up -d
```

The API is available at `http://localhost:5001`. The admin portal is at `/portal`.

### Environment variables

All variables are documented in [.env.example](.env.example). Key ones:

| Variable | Default | Notes |
|---|---|---|
| `REDLIB_API_PORT` | `5001` | Port the API listens on |
| `DATABASE_URL` | `sqlite:///./data/redlib.db` | SQLite path (on the named volume) |
| `PORTAL_SECRET_KEY` | — | Signs session cookies; use a long random string |
| `PORTAL_ADMIN_PASSWORD_HASH` | — | bcrypt hash; generate with `python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"` |

### Bumping the Redlib image digest

The Redlib backend is pinned by SHA digest in `docker-compose.yml`. To update it, find the new digest on [ghcr.io/silvenga/redlib](https://github.com/silvenga/redlib/pkgs/container/redlib) and edit the `image:` line in `docker-compose.yml` — it is a deliberate, separate change.
