"""Validation for user supplied recruitment and application links."""

from __future__ import annotations

from urllib.parse import urlsplit


def validate_link_url(value: object) -> str:
    """Accept a direct HTTP(S) URL only; an empty value means no link."""
    url = str(value or "").strip()
    if not url:
        return ""
    if len(url) > 2048 or any(char.isspace() or ord(char) < 32 for char in url):
        raise ValueError("链接过长或包含空白字符。")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("链接格式不正确。") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ValueError("链接需要以 http:// 或 https:// 开头，并包含网站地址。")
    if parsed.username or parsed.password or port == 0:
        raise ValueError("链接格式不正确。")
    return url
