"""
HTML sanitiser for API responses.

Post.body_html and Comment.body_html returned by this API are sanitised with
nh3 before serialisation. They are safe HTML subsets, not raw Reddit markup.
Do not pass them through an additional sanitiser.
"""

from __future__ import annotations

import nh3

ALLOWED_TAGS: set[str] = {
    "p", "br", "strong", "em", "ul", "ol", "li", "blockquote",
    "code", "pre", "a", "h1", "h2", "h3", "h4", "h5", "h6",
}
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {"a": {"href", "title"}}


def clean_html(html: str | None) -> str | None:
    if html is None:
        return None
    return nh3.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
