"""Input validation for redlib-api route parameters. No I/O — pure functions."""

from __future__ import annotations

import re

SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_\-]{1,50}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,50}$")
POST_ID_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")
SLUG_RE = re.compile(r"^[A-Za-z0-9_\-]{0,128}$")
AFTER_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{0,64}$")

SORTS: frozenset[str] = frozenset({"hot", "new", "top", "rising", "controversial"})
TIME_FILTERS: frozenset[str] = frozenset({"hour", "day", "week", "month", "year", "all"})


def validate_subreddit(value: str) -> str:
    if not SUBREDDIT_RE.match(value):
        raise ValueError("Invalid subreddit name: must match [A-Za-z0-9_-]{1,50}")
    return value


def validate_username(value: str) -> str:
    if not USERNAME_RE.match(value):
        raise ValueError("Invalid username: must match [A-Za-z0-9_-]{1,50}")
    return value


def validate_post_id(value: str) -> str:
    if not POST_ID_RE.match(value):
        raise ValueError("Invalid post ID: must match [A-Za-z0-9]{1,16}")
    return value


def validate_slug(value: str) -> str:
    if not SLUG_RE.match(value):
        raise ValueError("Invalid slug: must match [A-Za-z0-9_-]{0,128}")
    return value


def validate_sort(value: str) -> str:
    if value not in SORTS:
        raise ValueError(f"Invalid sort '{value}': must be one of {sorted(SORTS)}")
    return value


def validate_time_filter(value: str) -> str:
    if value not in TIME_FILTERS:
        raise ValueError(f"Invalid time_filter '{value}': must be one of {sorted(TIME_FILTERS)}")
    return value


def validate_after(value: str | None) -> str | None:
    if value is None:
        return None
    if not AFTER_TOKEN_RE.match(value):
        raise ValueError("Invalid after token: must match [A-Za-z0-9_-]{0,64}")
    return value


def validate_query(q: str, max_len: int = 256) -> str:
    if not q or not q.strip():
        raise ValueError("Search query cannot be empty")
    if len(q) > max_len:
        raise ValueError(f"Search query too long: max {max_len} characters")
    return q.strip()
