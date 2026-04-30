from __future__ import annotations

from redlib_api.sanitize import clean_html


def test_none_passthrough():
    assert clean_html(None) is None


def test_empty_string():
    assert clean_html("") == ""


def test_plain_text_unchanged():
    assert clean_html("hello world") == "hello world"


def test_safe_block_tags_preserved():
    result = clean_html("<p>Hello <strong>world</strong></p>")
    assert "<p>" in result
    assert "<strong>" in result
    assert "Hello" in result


def test_script_tag_stripped():
    result = clean_html("<p>safe</p><script>alert('xss')</script>")
    assert "<script>" not in result
    assert "alert" not in result
    assert "safe" in result


def test_onclick_attribute_stripped():
    result = clean_html('<p onclick="evil()">text</p>')
    assert "onclick" not in result
    assert "text" in result


def test_href_on_anchor_preserved():
    result = clean_html('<a href="https://example.com">link</a>')
    assert 'href="https://example.com"' in result


def test_style_attribute_stripped():
    result = clean_html('<a href="/foo" style="color:red">link</a>')
    assert "style" not in result
    assert 'href="/foo"' in result


def test_class_attribute_stripped():
    result = clean_html('<p class="post_body">content</p>')
    assert "class" not in result
    assert "content" in result


def test_div_tag_stripped_content_kept():
    result = clean_html("<div>content</div>")
    assert "<div" not in result
    assert "content" in result


def test_code_and_pre_preserved():
    result = clean_html("<pre><code>x = 1</code></pre>")
    assert "<pre>" in result
    assert "<code>" in result


def test_blockquote_preserved():
    result = clean_html("<blockquote>quoted text</blockquote>")
    assert "<blockquote>" in result
    assert "quoted text" in result


def test_headings_preserved():
    for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        result = clean_html(f"<{tag}>Title</{tag}>")
        assert f"<{tag}>" in result


def test_list_tags_preserved():
    result = clean_html("<ul><li>item</li></ul>")
    assert "<ul>" in result
    assert "<li>" in result


def test_em_preserved():
    result = clean_html("<em>emphasis</em>")
    assert "<em>" in result


def test_iframe_stripped():
    result = clean_html('<iframe src="https://evil.com"></iframe>')
    assert "<iframe" not in result


def test_img_stripped():
    result = clean_html('<img src="https://example.com/img.png" alt="test">')
    assert "<img" not in result


def test_nested_sanitization():
    html = '<div class="wrap"><p onclick="x()"><a href="/ok" target="_blank">link</a></p></div>'
    result = clean_html(html)
    assert "<div" not in result
    assert "onclick" not in result
    assert 'href="/ok"' in result
    assert "target" not in result
