"""
Core RedlibClient: single-backend HTTP client, rate limiting, HTML parsers, Pydantic models.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from bs4 import BeautifulSoup, NavigableString, Tag
from pydantic import BaseModel, Field

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RedlibError(Exception):
    """Base exception for all Redlib client errors."""


class RedlibConnectionError(RedlibError):
    """Local Redlib backend unreachable / 5xx."""


class RedlibTimeoutError(RedlibConnectionError):
    """Request to local Redlib backend timed out."""


class RedlibParseError(RedlibError):
    """Redlib returned HTML that could not be parsed into expected shape."""


class RedlibRateLimitError(RedlibError):
    """Backend returned a rate-limit response."""


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Comment(BaseModel):
    id: str | None = None
    author: str | None = None
    score: str | None = None
    body_html: str | None = None
    body_text: str | None = None
    created: datetime | None = None
    depth: int = 0
    replies: list[Comment] = Field(default_factory=list)


class Post(BaseModel):
    id: str | None = None
    title: str | None = None
    author: str | None = None
    subreddit: str | None = None
    score: str | None = None
    url: str | None = None
    external_url: str | None = None
    body_html: str | None = None
    body_text: str | None = None
    comment_count: str | None = None
    created: datetime | None = None
    flair: str | None = None
    nsfw: bool = False
    thumbnail_url: str | None = None
    media_urls: list[str] = Field(default_factory=list)


class SubredditInfo(BaseModel):
    name: str | None = None
    title: str | None = None
    description: str | None = None
    members: str | None = None
    active: str | None = None


class SearchResult(BaseModel):
    posts: list[Post] = Field(default_factory=list)
    next_page_token: str | None = None


class UserProfile(BaseModel):
    username: str | None = None
    karma: str | None = None
    created: datetime | None = None
    posts: list[Post] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Enforces a minimum gap between consecutive requests."""

    def __init__(self, rate_limit: float) -> None:
        self._min_gap = 1.0 / rate_limit if rate_limit > 0 else 0.0
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    def wait_sync(self) -> None:
        now = time.monotonic()
        gap = self._min_gap - (now - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    async def wait_async(self) -> None:
        async with self._lock:
            now = time.monotonic()
            gap = self._min_gap - (now - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = time.monotonic()


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------


def _text(tag: Tag | NavigableString | None) -> str | None:
    if not isinstance(tag, Tag):
        return None
    return tag.get_text(strip=True) or None


def _attr(tag: Tag | NavigableString | None, attr: str) -> str | None:
    if not isinstance(tag, Tag):
        return None
    val = tag.get(attr)
    if isinstance(val, list):
        val = " ".join(val)
    return val or None


def _parse_datetime(tag: Tag | NavigableString | None) -> datetime | None:
    if not isinstance(tag, Tag):
        return None
    ts = _attr(tag, "datetime") or _attr(tag, "title")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        pass
    # Redlib title attr format: "Apr 30 2026, 19:10:17 UTC"
    try:
        return datetime.strptime(ts, "%b %d %Y, %H:%M:%S UTC").replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        logger.debug("datetime_parse_failed", raw=ts)
        return None


def _after_token(soup: BeautifulSoup) -> str | None:
    """Extract the `after=` pagination token from next-page link."""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "after=" in href:
            m = re.search(r"after=([^&]+)", href)
            if m:
                return urllib.parse.unquote(m.group(1))
    return None


def _parse_post(div: Tag) -> Post:
    """Parse a single `div.post` into a Post model (defensive — never raises)."""
    try:
        post_id = _attr(div, "id")
        nsfw = "nsfw" in (div.get("class") or [])

        # Title link is the non-flair <a> inside <h2 class="post_title">
        title_tag: Tag | None = None
        post_url: str | None = None
        h2 = div.find("h2", class_="post_title")
        if isinstance(h2, Tag):
            for a in h2.find_all("a"):
                if "post_flair" not in (a.get("class") or []):
                    title_tag = a
                    post_url = _attr(a, "href")
                    break

        score_tag = div.find(class_="post_score")
        author_tag = div.find("a", class_="post_author")
        sub_tag = div.find("a", class_="post_subreddit")
        comments_tag = div.find("a", class_="post_comments")
        time_tag = div.find("span", class_="created")
        flair_tag = div.find("a", class_="post_flair")
        thumb_tag = div.find("img", class_="post_thumbnail")

        body_div = div.find("div", class_="post_body")
        body_html = str(body_div) if body_div else None
        body_text = _text(body_div) if body_div else None

        media_urls: list[str] = []
        for img in div.find_all("img", class_=lambda c: c and "media" in c):
            src = _attr(img, "src")
            if src:
                media_urls.append(src)

        return Post(
            id=post_id,
            title=_text(title_tag),
            author=_text(author_tag),
            subreddit=_text(sub_tag),
            score=_attr(score_tag, "title"),
            url=post_url,
            external_url=None,
            body_html=body_html,
            body_text=body_text,
            comment_count=_text(comments_tag),
            created=_parse_datetime(time_tag),
            flair=_text(flair_tag),
            nsfw=nsfw,
            thumbnail_url=_attr(thumb_tag, "src"),
            media_urls=media_urls,
        )
    except Exception as exc:
        logger.warning("post_parse_error", post_id=_attr(div, "id"), exc_info=exc)
        return Post()


def _parse_comment(div: Tag, depth: int = 0) -> Comment:
    """Recursively parse a `div.comment` (defensive — never raises)."""
    try:
        comment_id = _attr(div, "id")
        author_tag = div.find("a", class_="comment_author")
        score_tag = div.find("p", class_="comment_score")
        # post pages use <a class="created">, user pages use <span class="created">
        time_tag = div.find("a", class_="created") or div.find("span", class_="created")
        # post pages wrap in div.comment_body; user pages use div.md directly
        body_div = div.find("div", class_="comment_body") or div.find("div", class_="md")

        body_html = str(body_div) if body_div else None
        body_text = _text(body_div) if body_div else None

        replies: list[Comment] = []
        # replies live in <blockquote class="replies">, not a div
        replies_container = div.find("blockquote", class_="replies")
        if isinstance(replies_container, Tag):
            for child in replies_container.find_all("div", class_="comment", recursive=False):
                replies.append(_parse_comment(child, depth + 1))

        return Comment(
            id=comment_id,
            author=_text(author_tag),
            score=_attr(score_tag, "title"),
            body_html=body_html,
            body_text=body_text,
            created=_parse_datetime(time_tag),
            depth=depth,
            replies=replies,
        )
    except Exception as exc:
        logger.warning("comment_parse_error", depth=depth, exc_info=exc)
        return Comment()


def _parse_subreddit_info(soup: BeautifulSoup) -> SubredditInfo:
    """Parse sidebar into SubredditInfo (defensive — never raises)."""
    try:
        # silvenga fork renders <details id="sidebar">, not <div>
        sidebar_raw = soup.find(id="sidebar")
        if not isinstance(sidebar_raw, Tag):
            logger.debug("sidebar_not_found")
            return SubredditInfo()

        contents_raw = sidebar_raw.find("div", id="sidebar_contents")
        contents: Tag = contents_raw if isinstance(contents_raw, Tag) else sidebar_raw
        name_tag = contents.find(class_="subreddit_name") or contents.find("h1")
        title_tag = contents.find(class_="subreddit_title") or contents.find("h2")
        desc_tag = contents.find(class_="subreddit_description") or contents.find(
            "div", class_="md"
        )
        members_tag = contents.find(class_="subreddit_members")
        active_tag = contents.find(class_="subreddit_active")

        return SubredditInfo(
            name=_text(name_tag),
            title=_text(title_tag),
            description=_text(desc_tag),
            members=_text(members_tag),
            active=_text(active_tag),
        )
    except Exception as exc:
        logger.warning("sidebar_parse_error", exc_info=exc)
        return SubredditInfo()


def _parse_listing(html: str) -> tuple[list[Post], str | None, SubredditInfo]:
    soup = BeautifulSoup(html, "lxml")
    posts = [_parse_post(div) for div in soup.find_all("div", class_="post")]
    next_token = _after_token(soup)
    info = _parse_subreddit_info(soup)
    return posts, next_token, info


def _parse_post_page(html: str, url: str | None = None) -> tuple[Post | None, list[Comment]]:
    soup = BeautifulSoup(html, "lxml")

    post_div = soup.find("div", class_="post")
    post: Post | None = None
    if isinstance(post_div, Tag):
        post = _parse_post(post_div)
    elif post_div is None:
        logger.warning("post_div_missing", url=url)

    comments: list[Comment] = []
    comments_section = soup.find("div", id="commentarea") or soup.find("div", class_="comments")
    if isinstance(comments_section, Tag):
        for div in comments_section.find_all("div", class_="comment", recursive=False):
            comments.append(_parse_comment(div))

    return post, comments


def _parse_search(html: str) -> SearchResult:
    soup = BeautifulSoup(html, "lxml")
    posts = [_parse_post(div) for div in soup.find_all("div", class_="post")]
    next_token = _after_token(soup)
    return SearchResult(posts=posts, next_page_token=next_token)


def _parse_user(html: str) -> UserProfile:
    soup = BeautifulSoup(html, "lxml")

    username: str | None = None
    karma: str | None = None
    user_created: datetime | None = None

    title_tag = soup.select_one("#user_title")
    username = _text(title_tag)

    # #user_details layout: two <label> headers then two <div> values (karma, created)
    details = soup.select_one("#user_details")
    if isinstance(details, Tag):
        detail_divs = details.find_all("div", recursive=False)
        if detail_divs:
            karma = _text(detail_divs[0])
        if len(detail_divs) >= 2:
            raw = _text(detail_divs[1])
            if raw:
                try:
                    user_created = datetime.strptime(raw, "%b %d '%y").replace(tzinfo=UTC)
                except ValueError:
                    logger.debug("user_created_parse_failed", raw=raw)

    posts: list[Post] = []
    comments: list[Comment] = []

    for div in soup.find_all("div", class_="post"):
        posts.append(_parse_post(div))
    for div in soup.find_all("div", class_="comment"):
        comments.append(_parse_comment(div))

    return UserProfile(
        username=username,
        karma=karma,
        created=user_created,
        posts=posts,
        comments=comments,
    )


# ---------------------------------------------------------------------------
# RedlibClient
# ---------------------------------------------------------------------------

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class RedlibClient:
    """
    HTTP client that proxies requests through a local Redlib container,
    parses server-rendered HTML, and returns typed Pydantic models.
    """

    def __init__(
        self,
        base_url: str | None = None,
        rate_limit: float = 10.0,
        timeout: float = 10.0,
    ) -> None:
        if base_url is None:
            base_url = os.environ.get("REDLIB_BASE_URL", "http://localhost:8089")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._limiter = _RateLimiter(rate_limit)

        _headers = {"User-Agent": _BROWSER_UA}
        self._sync_client = httpx.Client(headers=_headers, timeout=timeout)
        self._async_client = httpx.AsyncClient(headers=_headers, timeout=timeout)

    @property
    def base_url(self) -> str:
        return self._base_url

    # ------------------------------------------------------------------
    # Low-level request (sync)
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = self._base_url + path
        self._limiter.wait_sync()
        try:
            resp = self._sync_client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise RedlibTimeoutError(f"Request timed out: {url}") from exc
        except httpx.HTTPError as exc:
            raise RedlibConnectionError(f"HTTP error reaching {url}: {exc}") from exc
        if resp.status_code == 429:
            raise RedlibRateLimitError(f"Rate limited by backend at {url}")
        if resp.status_code >= 500:
            raise RedlibConnectionError(f"Backend returned {resp.status_code} for {url}")
        resp.raise_for_status()
        return resp.text

    # ------------------------------------------------------------------
    # Low-level request (async)
    # ------------------------------------------------------------------

    async def _aget(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = self._base_url + path
        await self._limiter.wait_async()
        try:
            resp = await self._async_client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise RedlibTimeoutError(f"Request timed out: {url}") from exc
        except httpx.HTTPError as exc:
            raise RedlibConnectionError(f"HTTP error reaching {url}: {exc}") from exc
        if resp.status_code == 429:
            raise RedlibRateLimitError(f"Rate limited by backend at {url}")
        if resp.status_code >= 500:
            raise RedlibConnectionError(f"Backend returned {resp.status_code} for {url}")
        resp.raise_for_status()
        return resp.text

    # ------------------------------------------------------------------
    # Public sync API
    # ------------------------------------------------------------------

    def get_subreddit(
        self,
        subreddit: str,
        sort: str = "hot",
        limit: int = 25,
        after: str | None = None,
        time_filter: str | None = None,
    ) -> dict[str, Any]:
        """Return listing posts, next_page_token, and subreddit info."""
        path = f"/r/{subreddit}/{sort}"
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        if time_filter and sort in ("top", "controversial"):
            params["t"] = time_filter
        html = self._get(path, params)
        posts, next_token, info = _parse_listing(html)
        return {
            "posts": [p.model_dump() for p in posts],
            "next_page_token": next_token,
            "subreddit_info": info.model_dump(),
        }

    def get_post(self, path: str) -> dict[str, Any]:
        """Return a single post (no comments). `path` is the Redlib URL path."""
        html = self._get(path)
        post, _ = _parse_post_page(html, url=self._base_url + path)
        if post is None:
            raise RedlibParseError(f"No post found at {path}")
        return post.model_dump()

    def get_comments(self, path: str) -> dict[str, Any]:
        """Return post + full comment tree."""
        html = self._get(path)
        post, comments = _parse_post_page(html, url=self._base_url + path)
        return {
            "post": post.model_dump() if post else None,
            "comments": [c.model_dump() for c in comments],
        }

    def search(
        self,
        query: str,
        subreddit: str | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query}
        if subreddit:
            path = f"/r/{subreddit}/search"
            params["restrict_sr"] = "on"
        else:
            path = "/search"
        if after:
            params["after"] = after
        html = self._get(path, params)
        result = _parse_search(html)
        return result.model_dump()

    def get_user(self, username: str) -> dict[str, Any]:
        html = self._get(f"/user/{username}")
        profile = _parse_user(html)
        return profile.model_dump()

    def check_health(self) -> dict[str, Any]:
        """Probe the local Redlib backend. Returns {"base_url": str, "ok": bool}."""
        try:
            resp = self._sync_client.get(self._base_url + "/", timeout=5.0)
            ok = resp.status_code < 500
        except Exception:
            ok = False
        return {"base_url": self._base_url, "ok": ok}

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def aget_subreddit(
        self,
        subreddit: str,
        sort: str = "hot",
        limit: int = 25,
        after: str | None = None,
        time_filter: str | None = None,
    ) -> dict[str, Any]:
        path = f"/r/{subreddit}/{sort}"
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        if time_filter and sort in ("top", "controversial"):
            params["t"] = time_filter
        html = await self._aget(path, params)
        posts, next_token, info = _parse_listing(html)
        return {
            "posts": [p.model_dump() for p in posts],
            "next_page_token": next_token,
            "subreddit_info": info.model_dump(),
        }

    async def aget_post(self, path: str) -> dict[str, Any]:
        html = await self._aget(path)
        post, _ = _parse_post_page(html, url=self._base_url + path)
        if post is None:
            raise RedlibParseError(f"No post found at {path}")
        return post.model_dump()

    async def aget_comments(self, path: str) -> dict[str, Any]:
        html = await self._aget(path)
        post, comments = _parse_post_page(html, url=self._base_url + path)
        return {
            "post": post.model_dump() if post else None,
            "comments": [c.model_dump() for c in comments],
        }

    async def asearch(
        self,
        query: str,
        subreddit: str | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query}
        if subreddit:
            path = f"/r/{subreddit}/search"
            params["restrict_sr"] = "on"
        else:
            path = "/search"
        if after:
            params["after"] = after
        html = await self._aget(path, params)
        result = _parse_search(html)
        return result.model_dump()

    async def aget_user(self, username: str) -> dict[str, Any]:
        html = await self._aget(f"/user/{username}")
        profile = _parse_user(html)
        return profile.model_dump()

    async def acheck_health(self) -> dict[str, Any]:
        """Probe the local Redlib backend. Returns {"base_url": str, "ok": bool}."""
        try:
            resp = await self._async_client.get(self._base_url + "/", timeout=5.0)
            ok = resp.status_code < 500
        except Exception:
            ok = False
        return {"base_url": self._base_url, "ok": ok}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._sync_client.close()

    async def aclose(self) -> None:
        await self._async_client.aclose()

    def __enter__(self) -> RedlibClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    async def __aenter__(self) -> RedlibClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()
