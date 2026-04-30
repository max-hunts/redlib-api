from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import redlib_api.auth as auth_module
import redlib_api.server as server_module
from redlib_api.server import _cache, app


@pytest.fixture
async def fresh_db(tmp_path):
    db_path = tmp_path / "test.db"
    await auth_module.init_db(f"sqlite:///{db_path}")
    yield
    await auth_module.close_db()


@pytest.fixture
def mock_redlib_client():
    _post = {
        "id": "abc123",
        "title": "Test post",
        "author": "testuser",
        "subreddit": "python",
        "score": "42",
        "url": "/r/python/comments/abc123/test_post/",
        "external_url": None,
        "body_html": "<p>Hello <strong>world</strong></p>",
        "body_text": "Hello world",
        "comment_count": "5",
        "created": None,
        "flair": None,
        "nsfw": False,
        "thumbnail_url": None,
        "media_urls": [],
    }
    _comment = {
        "id": "c1",
        "author": "commenter",
        "score": "10",
        "body_html": "<p>Nice post</p>",
        "body_text": "Nice post",
        "created": None,
        "depth": 0,
        "replies": [],
    }
    client = MagicMock()
    client.aget_subreddit = AsyncMock(return_value={
        "posts": [_post],
        "next_page_token": "t3_next",
        "subreddit_info": {
            "name": "python", "title": "Python", "description": None,
            "members": "1M", "active": "500",
        },
    })
    client.aget_comments = AsyncMock(return_value={
        "post": _post,
        "comments": [_comment],
    })
    client.asearch = AsyncMock(return_value={
        "posts": [_post],
        "next_page_token": None,
    })
    client.aget_user = AsyncMock(return_value={
        "username": "testuser",
        "karma": "1000",
        "created": None,
        "posts": [_post],
        "comments": [_comment],
    })
    client.acheck_health = AsyncMock(return_value={
        "base_url": "http://redlib:8080",
        "ok": True,
    })
    return client


@pytest.fixture
async def api_token(fresh_db):
    return await auth_module.create_key("pytest-client", "test@example.com")


@pytest.fixture
async def http_client(fresh_db, mock_redlib_client, monkeypatch):
    monkeypatch.setattr(server_module, "_REDLIB_CLIENT", mock_redlib_client)
    _cache._store.clear()
    _cache._total_bytes = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
