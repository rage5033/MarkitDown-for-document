"""Lightweight YouTube URL detection and video-title lookup (no transcript fetch)."""
from __future__ import annotations

import html
import re

import requests

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_YOUTUBE_HOSTS = ("youtube.com", "youtu.be")


def is_youtube_url(text: str) -> bool:
    text = text.strip().lower()
    return text.startswith(("http://", "https://")) and any(h in text for h in _YOUTUBE_HOSTS)


def fetch_title(url: str, timeout: float = 6.0) -> str | None:
    """Fetch the video page title only, cheaper than a full MarkItDown conversion."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception:
        return None
    match = _TITLE_RE.search(resp.text)
    if not match:
        return None
    title = html.unescape(match.group(1)).strip()
    if title.endswith(" - YouTube"):
        title = title[: -len(" - YouTube")].strip()
    return title or None
