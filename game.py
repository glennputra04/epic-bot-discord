"""Daily puzzles and answer checking (no Discord code, so it's easy to test)."""

from __future__ import annotations

from functools import lru_cache

import crossword
import wordsearch

CROSSWORD = "crossword"
WORDSEARCH = "wordsearch"


@lru_cache(maxsize=8)
def puzzles_for(day: str) -> tuple[crossword.Crossword, wordsearch.WordSearch]:
    cw = crossword.generate(day)
    # Keep the two puzzles' answers different so "1. apple" can't be ambiguous.
    ws = wordsearch.generate(day, exclude={e.answer for e in cw.entries})
    return cw, ws


def check(
    day: str,
    number: int,
    direction: str | None,
    guess: str,
    only: str | None = None,
) -> tuple[str, str] | bool | None:
    """Check a parsed answer against today's puzzles.

    Returns ``(puzzle, key)`` when correct, ``False`` when the number exists but
    the answer is wrong, and ``None`` when no puzzle has that number (the bot
    ignores those messages). ``only`` restricts the check to one puzzle, e.g.
    when the player replied to the crossword message.
    """
    cw, ws = puzzles_for(day)
    found_number = False

    if only in (None, CROSSWORD):
        for entry in cw.entries:
            if entry.number == number and (direction is None or entry.direction == direction):
                found_number = True
                if entry.answer == guess:
                    return CROSSWORD, entry.key

    if only in (None, WORDSEARCH) and direction is None and 1 <= number <= len(ws.words):
        found_number = True
        if ws.words[number - 1][0] == guess:
            return WORDSEARCH, str(number)

    return False if found_number else None
