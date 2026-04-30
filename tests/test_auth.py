from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from redlib_api.auth import (
    _get_db,
    _hash_token,
    create_key,
    get_usage,
    list_keys,
    prune_usage_log,
    revoke_key,
)


class TestCreateKey:
    async def test_returns_plaintext_uuid(self, fresh_db):
        token = await create_key("test")
        assert len(token) == 36
        assert token.count("-") == 4

    async def test_hash_stored_not_plaintext(self, fresh_db):
        token = await create_key("test")
        db = _get_db()
        cursor = await db.execute("SELECT key_hash FROM api_keys WHERE name = 'test'")
        row = await cursor.fetchone()
        assert row is not None
        assert row["key_hash"] != token
        assert row["key_hash"] == _hash_token(token)

    async def test_key_active_by_default(self, fresh_db):
        await create_key("active-check")
        keys = await list_keys()
        key = next(k for k in keys if k.name == "active-check")
        assert key.is_active

    async def test_with_email(self, fresh_db):
        await create_key("with-email", "owner@example.com")
        keys = await list_keys()
        key = next(k for k in keys if k.name == "with-email")
        assert key.email == "owner@example.com"

    async def test_without_email(self, fresh_db):
        await create_key("no-email")
        keys = await list_keys()
        key = next(k for k in keys if k.name == "no-email")
        assert key.email is None

    async def test_unique_tokens(self, fresh_db):
        t1 = await create_key("a")
        t2 = await create_key("b")
        assert t1 != t2


class TestRevokeKey:
    async def test_revoke_sets_inactive(self, fresh_db):
        await create_key("revokable")
        keys = await list_keys()
        key = next(k for k in keys if k.name == "revokable")
        assert key.is_active

        await revoke_key(key.id)
        keys = await list_keys()
        key = next(k for k in keys if k.name == "revokable")
        assert not key.is_active

    async def test_revoke_does_not_delete_row(self, fresh_db):
        await create_key("kept")
        keys = await list_keys()
        key = next(k for k in keys if k.name == "kept")
        await revoke_key(key.id)
        keys = await list_keys()
        assert any(k.name == "kept" for k in keys)


class TestListKeys:
    async def test_empty(self, fresh_db):
        assert await list_keys() == []

    async def test_multiple(self, fresh_db):
        await create_key("alpha")
        await create_key("beta")
        await create_key("gamma")
        keys = await list_keys()
        names = [k.name for k in keys]
        assert "alpha" in names
        assert "beta" in names
        assert "gamma" in names

    async def test_ordered_by_id(self, fresh_db):
        await create_key("first")
        await create_key("second")
        keys = await list_keys()
        assert keys[0].id < keys[1].id


class TestGetUsage:
    async def test_empty_for_new_key(self, fresh_db):
        await create_key("usage-test")
        keys = await list_keys()
        key = next(k for k in keys if k.name == "usage-test")
        usage = await get_usage(key.id, datetime.now(UTC) - timedelta(days=7))
        assert usage.requests_total == 0
        assert usage.requests_by_day == {}

    async def test_counts_usage_log_rows(self, fresh_db):
        await create_key("counted")
        keys = await list_keys()
        key = next(k for k in keys if k.name == "counted")

        db = _get_db()
        now = datetime.now(UTC).isoformat()
        for _ in range(3):
            await db.execute(
                "INSERT INTO usage_log (key_id, ts, endpoint, status_code) VALUES (?, ?, ?, ?)",
                (key.id, now, "/r/python", 200),
            )
        await db.commit()

        usage = await get_usage(key.id, datetime.now(UTC) - timedelta(days=1))
        assert usage.requests_total == 3

    async def test_since_filter(self, fresh_db):
        await create_key("filtered")
        keys = await list_keys()
        key = next(k for k in keys if k.name == "filtered")

        db = _get_db()
        old_ts = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        new_ts = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT INTO usage_log (key_id, ts, endpoint, status_code) VALUES (?, ?, ?, ?)",
            (key.id, old_ts, "/r/python", 200),
        )
        await db.execute(
            "INSERT INTO usage_log (key_id, ts, endpoint, status_code) VALUES (?, ?, ?, ?)",
            (key.id, new_ts, "/r/python", 200),
        )
        await db.commit()

        usage = await get_usage(key.id, datetime.now(UTC) - timedelta(days=1))
        assert usage.requests_total == 1


class TestPruneUsageLog:
    async def test_prunes_old_rows(self, fresh_db):
        await create_key("prune-test")
        keys = await list_keys()
        key = next(k for k in keys if k.name == "prune-test")

        db = _get_db()
        old_ts = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        recent_ts = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT INTO usage_log (key_id, ts, endpoint, status_code) VALUES (?, ?, ?, ?)",
            (key.id, old_ts, "/r/python", 200),
        )
        await db.execute(
            "INSERT INTO usage_log (key_id, ts, endpoint, status_code) VALUES (?, ?, ?, ?)",
            (key.id, recent_ts, "/r/python", 200),
        )
        await db.commit()

        deleted = await prune_usage_log(older_than_days=30)
        assert deleted == 1

        cursor = await db.execute(
            "SELECT COUNT(*) FROM usage_log WHERE key_id = ?", (key.id,)
        )
        row = await cursor.fetchone()
        assert row[0] == 1

    async def test_prune_nothing_to_delete(self, fresh_db):
        deleted = await prune_usage_log(older_than_days=30)
        assert deleted == 0
