"""Puzzel.org Puzzle API client (https://puzzel.org/en/developers/puzzle-api).

The API builds a playable activity from our own words/clues and returns the
URL it plays at. The bot still knows the answers (it chose the words), which is
how it checks replies in Discord.

Endpoint paths and body fields can be overridden with environment variables in
case they differ from the defaults below. If a request fails, Puzzel's error
response is logged and the puzzle is still posted in Discord, just without a link.
"""

from __future__ import annotations

import logging
import os

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = os.getenv("PUZZEL_BASE_URL", "https://puzzel.org/api/public/v1").rstrip("/")
API_KEY = os.getenv("PUZZEL_API_KEY", "")
EMAIL = os.getenv("PUZZEL_EMAIL", "")
LANGUAGE = os.getenv("PUZZEL_LANGUAGE", "en")
CROSSWORD_PATH = os.getenv("PUZZEL_CROSSWORD_PATH", "crossword")
WORDSEARCH_PATH = os.getenv("PUZZEL_WORDSEARCH_PATH", "wordseeker")


def enabled() -> bool:
    return bool(API_KEY and EMAIL)


async def _create(path: str, title: str, items: list[dict]) -> str | None:
    if not enabled():
        return None
    body = {"key": API_KEY, "email": EMAIL, "title": title, "language": LANGUAGE, "items": items}
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{BASE_URL}/{path}", json=body) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400 or not isinstance(data, dict) or not data.get("url"):
                    log.warning("Puzzel %s failed (HTTP %s): %s", path, resp.status, data)
                    return None
                return data["url"]
    except Exception as exc:
        log.warning("Puzzel %s request error: %s", path, exc)
        return None


async def create_crossword(title: str, entries: list[tuple[str, str]]) -> str | None:
    """``entries`` is a list of ``(answer, clue)``."""
    return await _create(CROSSWORD_PATH, title, [{"answer": a, "clue": c} for a, c in entries])


async def create_wordsearch(title: str, words: list[str]) -> str | None:
    return await _create(WORDSEARCH_PATH, title, [{"answer": w} for w in words])
