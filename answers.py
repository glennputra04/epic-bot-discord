"""Parse answer messages like ``1. apple``, ``3 down: Banana`` or ``2a ||python||``."""

from __future__ import annotations

import re

_PATTERN = re.compile(
    r"^\s*#?(\d{1,2})\s*"
    r"(?:(across|down|a|d)(?![a-z]))?"  # optional crossword direction
    r"\s*[.):=\-]*\s*"
    r"(.+?)\s*$",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Upper-case and keep letters only, so case, spaces and punctuation don't matter."""
    return "".join(ch for ch in text.upper() if ch.isalpha())


def parse(text: str) -> tuple[int, str | None, str] | None:
    """Return ``(number, direction or None, normalized guess)`` or ``None`` if it isn't an answer."""
    text = text.replace("||", "").strip()  # allow spoiler tags
    match = _PATTERN.match(text)
    if not match:
        return None
    number, direction, raw_guess = int(match[1]), match[2], match[3]
    if len(raw_guess.split()) > 2:  # answers are single words; ignore normal chatter
        return None
    guess = normalize(raw_guess)
    if len(guess) < 2:  # all puzzle words have 3+ letters; this also skips things like "1a"
        return None
    if direction:
        direction = "across" if direction.lower().startswith("a") else "down"
    return number, direction, guess
