from __future__ import annotations

import pytest

from redlib_api.validators import (
    validate_after,
    validate_post_id,
    validate_query,
    validate_slug,
    validate_sort,
    validate_subreddit,
    validate_time_filter,
    validate_username,
)


class TestSubreddit:
    def test_simple(self):
        assert validate_subreddit("python") == "python"

    def test_underscores_hyphens(self):
        assert validate_subreddit("the_front_page-of") == "the_front_page-of"

    def test_max_length(self):
        assert validate_subreddit("a" * 50) == "a" * 50

    def test_too_long(self):
        with pytest.raises(ValueError):
            validate_subreddit("a" * 51)

    def test_empty(self):
        with pytest.raises(ValueError):
            validate_subreddit("")

    @pytest.mark.parametrize("bad", ["r/python", "sub?q=1", "sub#anchor", "sub space", "sub\x00"])
    def test_rejects_special_chars(self, bad):
        with pytest.raises(ValueError):
            validate_subreddit(bad)


class TestUsername:
    def test_simple(self):
        assert validate_username("user123") == "user123"

    def test_underscores_hyphens(self):
        assert validate_username("user_name-1") == "user_name-1"

    def test_empty(self):
        with pytest.raises(ValueError):
            validate_username("")

    def test_slash(self):
        with pytest.raises(ValueError):
            validate_username("user/name")

    def test_too_long(self):
        with pytest.raises(ValueError):
            validate_username("u" * 51)


class TestPostId:
    def test_alphanumeric(self):
        assert validate_post_id("abc123") == "abc123"
        assert validate_post_id("ABC123DEF456") == "ABC123DEF456"

    def test_max_length(self):
        assert validate_post_id("a" * 16) == "a" * 16

    def test_too_long(self):
        with pytest.raises(ValueError):
            validate_post_id("a" * 17)

    def test_empty(self):
        with pytest.raises(ValueError):
            validate_post_id("")

    def test_underscore_rejected(self):
        with pytest.raises(ValueError):
            validate_post_id("abc_def")

    def test_slash_rejected(self):
        with pytest.raises(ValueError):
            validate_post_id("abc/def")


class TestSlug:
    def test_simple(self):
        assert validate_slug("my_post_slug") == "my_post_slug"

    def test_empty_allowed(self):
        assert validate_slug("") == ""

    def test_max_length(self):
        assert validate_slug("a" * 128) == "a" * 128

    def test_too_long(self):
        with pytest.raises(ValueError):
            validate_slug("a" * 129)

    def test_slash_rejected(self):
        with pytest.raises(ValueError):
            validate_slug("slug/path")


class TestSort:
    @pytest.mark.parametrize("s", ["hot", "new", "top", "rising", "controversial"])
    def test_valid(self, s):
        assert validate_sort(s) == s

    def test_invalid(self):
        with pytest.raises(ValueError):
            validate_sort("best")

    def test_case_sensitive(self):
        with pytest.raises(ValueError):
            validate_sort("Hot")

    def test_empty(self):
        with pytest.raises(ValueError):
            validate_sort("")


class TestTimeFilter:
    @pytest.mark.parametrize("tf", ["hour", "day", "week", "month", "year", "all"])
    def test_valid(self, tf):
        assert validate_time_filter(tf) == tf

    def test_invalid(self):
        with pytest.raises(ValueError):
            validate_time_filter("today")

    def test_empty(self):
        with pytest.raises(ValueError):
            validate_time_filter("")


class TestAfter:
    def test_none_passthrough(self):
        assert validate_after(None) is None

    def test_valid_token(self):
        assert validate_after("t3_abc123") == "t3_abc123"

    def test_empty_allowed(self):
        assert validate_after("") == ""

    def test_too_long(self):
        with pytest.raises(ValueError):
            validate_after("a" * 65)

    def test_special_chars_rejected(self):
        with pytest.raises(ValueError):
            validate_after("tok?evil=1")


class TestQuery:
    def test_simple(self):
        assert validate_query("asyncio python") == "asyncio python"

    def test_strips_outer_whitespace(self):
        assert validate_query("  hello  ") == "hello"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            validate_query("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            validate_query("   ")

    def test_default_max_len(self):
        with pytest.raises(ValueError):
            validate_query("x" * 257)

    def test_exactly_at_limit(self):
        assert validate_query("x" * 256) == "x" * 256

    def test_custom_max_len(self):
        with pytest.raises(ValueError):
            validate_query("hello", max_len=4)
