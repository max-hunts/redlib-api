# /audit

You are a code auditor reviewing this project for safety, security, and reliability issues. This is not an enterprise app, but it runs as a public-facing HTTP service that makes outbound requests based on user input, so some things matter.

## What to check

### Input handling
- Subreddit names, usernames, post IDs, and sort parameters come from API callers and get embedded in URLs sent to external instances. Check that these are validated or safely encoded — no path traversal, no injection into query strings.
- `after` pagination tokens come from user input and are forwarded as-is. Are they bounded or sanitised?

### SSRF risk
- The `instances` parameter can be user-supplied. Check whether there is any restriction on what URLs are accepted — an attacker could point the client at an internal service.
- If the server exposes any endpoint that lets callers specify an instance URL, flag it.

### Parsed content
- Body HTML from Reddit posts is parsed and re-served. Check whether it is passed through as-is or sanitised. Unsanitised HTML returned in an API response can cause XSS if a frontend renders it.

### Error and exception handling
- Verify that `RedlibConnectionError`, `RedlibParseError`, and `RedlibRateLimitError` are all caught at the FastAPI layer and return appropriate HTTP status codes (not 500s with stack traces).
- Check that no internal paths, instance URLs, or raw exception messages are leaked to API callers in production.

### Rate limiting
- Is the per-instance rate limiter actually enforced under concurrent async calls, or is it only safe in single-threaded use?

### Logging
- Check that debug logs don't emit response bodies or anything that could contain PII (Reddit usernames, post content) at INFO level or above in production.

### Dependencies
- Scan `requirements.txt` for obviously outdated or unmaintained packages. Note any with known CVEs if you recognise them — don't fabricate CVE numbers.

## How to report

For each issue found: **severity** (low/medium/high), **location** (file + line if possible), **what the problem is**, and a **concrete fix**. Skip anything that's acceptable for a non-enterprise project of this scope. Don't pad the report.
