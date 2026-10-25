"""Question of the Day from the boneitis.org API (https://boneitis.org/docs).

Only the question is posted; there's no answer to check.
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger(__name__)

API_BASE = "https://boneitis.org"


async def fetch_question(category: str = "general") -> dict | None:
    """Return ``{"question", "category", "date"}`` for today, or ``None`` if the API fails."""
    url = f"{API_BASE}/today"
    for attempt in range(3):
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params={"category": category}) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status != 200 or "question" not in data:
                        raise ValueError(f"HTTP {resp.status}: {data.get('error', data)}")
                    return data
        except Exception as exc:
            log.warning("boneitis.org request failed (attempt %d/3): %s", attempt + 1, exc)
    return None
