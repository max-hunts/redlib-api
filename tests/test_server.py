from __future__ import annotations

from datetime import UTC, datetime

import redlib_api.auth as auth_module
from redlib_api.client import (
    RedlibConnectionError,
    RedlibParseError,
    RedlibRateLimitError,
    RedlibTimeoutError,
)


class TestHealth:
    async def test_ok(self, http_client):
        r = await http_client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["redlib"]["ok"] is True

    async def test_no_auth_required(self, http_client):
        r = await http_client.get("/health")
        assert r.status_code == 200

    async def test_degraded_when_client_none(self, fresh_db, monkeypatch):
        from httpx import ASGITransport, AsyncClient

        import redlib_api.server as server_module
        from redlib_api.server import _cache, app

        monkeypatch.setattr(server_module, "_REDLIB_CLIENT", None)
        _cache._store.clear()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "degraded"


class TestAuthEnforcement:
    async def test_missing_header_is_401(self, http_client):
        r = await http_client.get("/r/python")
        assert r.status_code == 401
        assert r.headers["content-type"] == "application/problem+json"

    async def test_problem_json_shape(self, http_client):
        r = await http_client.get("/r/python")
        data = r.json()
        assert data["status"] == 401
        assert "title" in data
        assert "detail" in data

    async def test_rate_limit_headers_on_401(self, http_client):
        r = await http_client.get("/r/python")
        assert "X-RateLimit-Limit" in r.headers
        assert "X-RateLimit-Remaining" in r.headers
        assert "X-RateLimit-Reset" in r.headers

    async def test_empty_bearer_is_401(self, http_client):
        r = await http_client.get("/r/python", headers={"Authorization": "Bearer "})
        assert r.status_code == 401

    async def test_malformed_scheme_is_401(self, http_client):
        r = await http_client.get("/r/python", headers={"Authorization": "Token abc"})
        assert r.status_code == 401

    async def test_invalid_token_is_401(self, http_client):
        r = await http_client.get("/r/python", headers={"Authorization": "Bearer not-a-real-key"})
        assert r.status_code == 401

    async def test_revoked_token_is_401(self, http_client, api_token, fresh_db):
        keys = await auth_module.list_keys()
        await auth_module.revoke_key(keys[0].id)
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 401

    async def test_valid_token_succeeds(self, http_client, api_token):
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 200


class TestRateLimitHeaders:
    async def test_present_on_success(self, http_client, api_token):
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 200
        assert "X-RateLimit-Limit" in r.headers
        assert "X-RateLimit-Remaining" in r.headers
        assert "X-RateLimit-Reset" in r.headers

    async def test_remaining_decrements(self, http_client, api_token):
        auth = {"Authorization": f"Bearer {api_token}"}
        r1 = await http_client.get("/r/python", headers=auth)
        r2 = await http_client.get("/r/rust", headers=auth)
        assert int(r2.headers["X-RateLimit-Remaining"]) < int(r1.headers["X-RateLimit-Remaining"])

    async def test_minute_rate_limit_enforced(self, http_client, api_token, fresh_db):
        keys = await auth_module.list_keys()
        key_id = keys[0].id
        db = auth_module._get_db()
        now = datetime.now(UTC).isoformat()
        for _ in range(60):
            await db.execute(
                "INSERT INTO usage_log (key_id, ts, endpoint, status_code) VALUES (?, ?, ?, ?)",
                (key_id, now, "/r/python", 200),
            )
        await db.commit()

        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert r.headers["content-type"] == "application/problem+json"

    async def test_day_rate_limit_enforced(self, http_client, api_token, fresh_db):
        keys = await auth_module.list_keys()
        key_id = keys[0].id
        db = auth_module._get_db()
        today_start = (
            datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        )
        for _ in range(1000):
            await db.execute(
                "INSERT INTO usage_log (key_id, ts, endpoint, status_code) VALUES (?, ?, ?, ?)",
                (key_id, today_start, "/r/python", 200),
            )
        await db.commit()

        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 429


class TestSubredditRoute:
    async def test_returns_200(self, http_client, api_token):
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 200
        data = r.json()
        assert "posts" in data

    async def test_limit_too_high_is_422(self, http_client, api_token):
        r = await http_client.get(
            "/r/python?limit=200", headers={"Authorization": f"Bearer {api_token}"}
        )
        assert r.status_code == 422

    async def test_limit_zero_is_422(self, http_client, api_token):
        r = await http_client.get(
            "/r/python?limit=0", headers={"Authorization": f"Bearer {api_token}"}
        )
        assert r.status_code == 422

    async def test_invalid_sort_is_400(self, http_client, api_token):
        r = await http_client.get(
            "/r/python?sort=viral", headers={"Authorization": f"Bearer {api_token}"}
        )
        assert r.status_code == 400

    async def test_invalid_time_filter_is_400(self, http_client, api_token):
        r = await http_client.get(
            "/r/python?sort=top&time_filter=today",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert r.status_code == 400

    async def test_body_html_sanitized(self, http_client, api_token, mock_redlib_client):
        mock_redlib_client.aget_subreddit.return_value = {
            "posts": [
                {
                    "id": "x1",
                    "title": "T",
                    "author": "u",
                    "subreddit": "python",
                    "score": "1",
                    "url": None,
                    "external_url": None,
                    "body_html": "<p>safe</p><script>evil()</script>",
                    "body_text": "safe",
                    "comment_count": "0",
                    "created": None,
                    "flair": None,
                    "nsfw": False,
                    "thumbnail_url": None,
                    "media_urls": [],
                }
            ],
            "next_page_token": None,
            "subreddit_info": {},
        }
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 200
        body = r.text
        assert "<script>" not in body


class TestCommentsRoute:
    async def test_returns_200(self, http_client, api_token):
        r = await http_client.get(
            "/r/python/comments/abc123/test_post",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "post" in data
        assert "comments" in data

    async def test_invalid_post_id_is_400(self, http_client, api_token):
        r = await http_client.get(
            "/r/python/comments/bad!id/slug",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert r.status_code == 400

    async def test_body_html_sanitized(self, http_client, api_token, mock_redlib_client):
        mock_redlib_client.aget_comments.return_value = {
            "post": {
                "id": "abc",
                "title": "T",
                "author": "u",
                "subreddit": "s",
                "score": "1",
                "url": None,
                "external_url": None,
                "body_html": "<p>ok</p><script>bad()</script>",
                "body_text": "ok",
                "comment_count": "0",
                "created": None,
                "flair": None,
                "nsfw": False,
                "thumbnail_url": None,
                "media_urls": [],
            },
            "comments": [
                {
                    "id": "c1",
                    "author": "u",
                    "score": "1",
                    "body_html": "<em>fine</em><script>bad()</script>",
                    "body_text": "fine",
                    "created": None,
                    "depth": 0,
                    "replies": [],
                }
            ],
        }
        r = await http_client.get(
            "/r/python/comments/abc123/slug",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert "<script>" not in r.text


class TestSearchRoute:
    async def test_returns_200(self, http_client, api_token):
        r = await http_client.get(
            "/search?q=python", headers={"Authorization": f"Bearer {api_token}"}
        )
        assert r.status_code == 200
        assert "posts" in r.json()

    async def test_missing_q_is_422(self, http_client, api_token):
        r = await http_client.get("/search", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 422

    async def test_empty_q_is_400(self, http_client, api_token):
        r = await http_client.get("/search?q=+", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 400


class TestUserRoute:
    async def test_returns_200(self, http_client, api_token):
        r = await http_client.get(
            "/user/testuser", headers={"Authorization": f"Bearer {api_token}"}
        )
        assert r.status_code == 200
        assert "username" in r.json()

    async def test_invalid_username_is_400(self, http_client, api_token):
        r = await http_client.get(
            "/user/bad/user", headers={"Authorization": f"Bearer {api_token}"}
        )
        assert r.status_code in (400, 404)


class TestErrorMapping:
    async def test_timeout_is_504(self, http_client, api_token, mock_redlib_client):
        mock_redlib_client.aget_subreddit.side_effect = RedlibTimeoutError("timed out")
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 504
        assert r.headers["content-type"] == "application/problem+json"

    async def test_connection_error_is_503(self, http_client, api_token, mock_redlib_client):
        mock_redlib_client.aget_subreddit.side_effect = RedlibConnectionError("unreachable")
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 503

    async def test_parse_error_is_502(self, http_client, api_token, mock_redlib_client):
        mock_redlib_client.aget_subreddit.side_effect = RedlibParseError("bad html")
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 502

    async def test_backend_rate_limit_is_502(self, http_client, api_token, mock_redlib_client):
        mock_redlib_client.aget_subreddit.side_effect = RedlibRateLimitError("backend 429")
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 502

    async def test_no_stack_trace_in_body(self, http_client, api_token, mock_redlib_client):
        mock_redlib_client.aget_subreddit.side_effect = RedlibConnectionError("boom")
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        body = r.text
        assert "Traceback" not in body
        assert "redlib_api" not in body

    async def test_timeout_is_not_503(self, http_client, api_token, mock_redlib_client):
        mock_redlib_client.aget_subreddit.side_effect = RedlibTimeoutError("timed out")
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code != 503


class TestCaching:
    async def test_second_request_uses_cache(self, http_client, api_token, mock_redlib_client):
        auth = {"Authorization": f"Bearer {api_token}"}
        await http_client.get("/r/python", headers=auth)
        await http_client.get("/r/python", headers=auth)
        assert mock_redlib_client.aget_subreddit.call_count == 1

    async def test_different_params_bypass_cache(self, http_client, api_token, mock_redlib_client):
        auth = {"Authorization": f"Bearer {api_token}"}
        await http_client.get("/r/python?sort=hot", headers=auth)
        await http_client.get("/r/python?sort=new", headers=auth)
        assert mock_redlib_client.aget_subreddit.call_count == 2

    async def test_response_time_header_present(self, http_client, api_token):
        r = await http_client.get("/r/python", headers={"Authorization": f"Bearer {api_token}"})
        assert "X-Response-Time" in r.headers
