"""Admin portal: bcrypt login, session cookie, key management UI."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
import bcrypt as _bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware

from redlib_api.auth import KeyInfo, UsageSummary, create_key, get_usage, list_keys, revoke_key

logger = structlog.get_logger(__name__)

_SECRET_KEY = os.environ.get("PORTAL_SECRET_KEY", "changeme")
_PASSWORD_HASH = os.environ.get("PORTAL_ADMIN_PASSWORD_HASH", "")
_COOKIE_NAME = "portal_session"
_SESSION_MAX_AGE = 8 * 3600  # 8 hours
_FLASH_COOKIE = "_flash_token"
_FLASH_MAX_AGE = 60  # seconds — survives one redirect + page load

_signer = URLSafeTimedSerializer(_SECRET_KEY)

_templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "portal")
)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _sign_session() -> str:
    return _signer.dumps("admin")


def _verify_session(token: str) -> bool:
    try:
        _signer.loads(token, max_age=_SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def _set_session_cookie(response: Response) -> None:
    response.set_cookie(
        _COOKIE_NAME,
        _sign_session(),
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # proxy handles TLS; flag set to False for plain-HTTP upstream
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(_COOKIE_NAME, httponly=True, samesite="lax")


def _set_flash_token(response: Response, token: str) -> None:
    response.set_cookie(
        _FLASH_COOKIE,
        _signer.dumps(token),
        max_age=_FLASH_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def _read_flash_token(request: Request) -> str:
    """Verify and return the flash token from the request cookie (does not clear it)."""
    signed = request.cookies.get(_FLASH_COOKIE, "")
    if not signed:
        return ""
    try:
        return _signer.loads(signed, max_age=_FLASH_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return ""


# ---------------------------------------------------------------------------
# Auth middleware (portal-internal)
# ---------------------------------------------------------------------------


class _RequireLoginMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path.rstrip("/").endswith("/login"):
            return await call_next(request)

        token = request.cookies.get(_COOKIE_NAME, "")
        if not _verify_session(token):
            login_url = str(request.url_for("login_page"))
            return RedirectResponse(url=login_url, status_code=303)

        return await call_next(request)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

portal_app = FastAPI(title="redlib-api admin portal")
portal_app.add_middleware(_RequireLoginMiddleware)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@portal_app.get("/login", response_class=HTMLResponse, name="login_page")
async def login_get(request: Request, error: str = "") -> HTMLResponse:
    return _templates.TemplateResponse(
        request, "login.html", {"error": error}
    )


@portal_app.post("/login")
async def login_post(
    request: Request,
    password: str = Form(...),
) -> Response:
    if not _PASSWORD_HASH:
        logger.warning("portal_login_attempted_no_hash_configured")
        return RedirectResponse(
            url=str(request.url_for("login_page")) + "?error=Portal+not+configured",
            status_code=303,
        )

    if not _bcrypt.checkpw(password.encode(), _PASSWORD_HASH.encode()):
        logger.warning("portal_login_failed")
        return RedirectResponse(
            url=str(request.url_for("login_page")) + "?error=Invalid+password",
            status_code=303,
        )

    logger.info("portal_login_success")
    response = RedirectResponse(url=str(request.url_for("key_list")), status_code=303)
    _set_session_cookie(response)
    return response


@portal_app.post("/logout")
async def logout(request: Request) -> Response:
    response = RedirectResponse(url=str(request.url_for("login_page")), status_code=303)
    _clear_session_cookie(response)
    return response


# ---------------------------------------------------------------------------
# Key list
# ---------------------------------------------------------------------------


@portal_app.get("/", response_class=HTMLResponse, name="key_list")
async def key_list(request: Request) -> HTMLResponse:
    created_token = _read_flash_token(request)

    keys = await list_keys()
    since = datetime.now(UTC) - timedelta(days=1)
    summaries: dict[int, UsageSummary] = {}
    for key in keys:
        summaries[key.id] = await get_usage(key.id, since)

    resp = _templates.TemplateResponse(
        request,
        "index.html",
        {"keys": keys, "summaries": summaries, "created_token": created_token},
    )
    if created_token:
        resp.delete_cookie(_FLASH_COOKIE, httponly=True, samesite="lax")
    return resp


# ---------------------------------------------------------------------------
# Create key
# ---------------------------------------------------------------------------


@portal_app.post("/keys")
async def create_key_post(
    request: Request,
    name: str = Form(...),
    email: str = Form(default=""),
) -> Response:
    name = name.strip()
    email_val: str | None = email.strip() or None
    if not name:
        keys = await list_keys()
        since = datetime.now(UTC) - timedelta(days=1)
        summaries: dict[int, UsageSummary] = {}
        for key in keys:
            summaries[key.id] = await get_usage(key.id, since)
        return _templates.TemplateResponse(
            request,
            "index.html",
            {"keys": keys, "summaries": summaries, "created_token": "", "error": "Name is required"},
            status_code=422,
        )

    token = await create_key(name, email_val)
    logger.info("portal_key_created", name=name)
    resp = RedirectResponse(url=str(request.url_for("key_list")), status_code=303)
    _set_flash_token(resp, token)
    return resp


# ---------------------------------------------------------------------------
# Revoke key
# ---------------------------------------------------------------------------


@portal_app.post("/keys/{key_id}/revoke")
async def revoke_key_post(request: Request, key_id: int) -> Response:
    await revoke_key(key_id)
    logger.info("portal_key_revoked", key_id=key_id)
    return RedirectResponse(url=str(request.url_for("key_list")), status_code=303)


# ---------------------------------------------------------------------------
# Key detail
# ---------------------------------------------------------------------------


@portal_app.get("/keys/{key_id}", response_class=HTMLResponse, name="key_detail")
async def key_detail(request: Request, key_id: int) -> HTMLResponse:
    keys = await list_keys()
    key: KeyInfo | None = next((k for k in keys if k.id == key_id), None)
    if key is None:
        return _templates.TemplateResponse(
            request, "not_found.html", {}, status_code=404
        )

    since = datetime.now(UTC) - timedelta(days=7)
    usage = await get_usage(key_id, since)

    # Build a full 7-day series (fill gaps with 0)
    today = datetime.now(UTC).date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    chart_data = [{"day": d, "count": usage.requests_by_day.get(d, 0)} for d in days]

    return _templates.TemplateResponse(
        request,
        "key_detail.html",
        {"key": key, "usage": usage, "chart_data": chart_data},
    )
