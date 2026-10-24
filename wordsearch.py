"""Daily word search generator.

Words come from ``words.json``. Players get the clues, not the words, so they
have to work out each word and find it in the grid. Same date = same puzzle.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass

from crossword import load_words

DIRECTIONS = [(0, 1), (1, 0), (1, 1), (-1, 1), (0, -1), (-1, 0), (-1, -1), (1, -1)]


@dataclass
class WordSearch:
    day: str
    size: int
    grid: list[list[str]]
    words: list[tuple[str, str]]  # (answer, clue), numbered from 1
    cells: list[list[tuple[int, int]]]  # grid cells for each word

    def render(self, found: set[int] | None = None, reveal: bool = False) -> str:
        """Plain grid by default. ``found`` (1-based word numbers) shows those words in
        lower case; ``reveal`` hides every letter that isn't part of a word."""
        if reveal:
            keep = {pos for cells in self.cells for pos in cells}
            return "\n".join(
                " ".join(ch if (r, c) in keep else "·" for c, ch in enumerate(row))
                for r, row in enumerate(self.grid)
            )
        marked = {pos for i, cells in enumerate(self.cells, 1) if found and i in found for pos in cells}
        return "\n".join(
            " ".join(ch.lower() if (r, c) in marked else ch for c, ch in enumerate(row))
            for r, row in enumerate(self.grid)
        )

    def clues_text(self) -> str:
        return "\n".join(f"`{i:>2}.` {clue} ({len(word)})" for i, (word, clue) in enumerate(self.words, 1))


def generate(day: str, count: int = 8, size: int = 12, exclude: set[str] | None = None) -> WordSearch:
    rng = random.Random(f"wordsearch-{day}")
    pool = [(w, c) for w, c in load_words() if 4 <= len(w) <= size and w not in (exclude or set())]
    rng.shuffle(pool)

    grid: list[list[str | None]] = [[None] * size for _ in range(size)]
    words: list[tuple[str, str]] = []
    placements: list[list[tuple[int, int]]] = []

    for word, clue in pool:
        if len(words) >= count:
            break
        if any(word in w or w in word for w, _ in words):  # avoid words hiding inside each other
            continue
        for _ in range(200):
            dr, dc = rng.choice(DIRECTIONS)
            r, c = rng.randrange(size), rng.randrange(size)
            cells = [(r + dr * i, c + dc * i) for i in range(len(word))]
            if all(0 <= rr < size and 0 <= cc < size and grid[rr][cc] in (None, ch)
                   for (rr, cc), ch in zip(cells, word)):
                for (rr, cc), ch in zip(cells, word):
                    grid[rr][cc] = ch
                words.append((word, clue))
                placements.append(cells)
                break

    filled = [[ch or rng.choice(string.ascii_uppercase) for ch in row] for row in grid]
    return WordSearch(day=day, size=size, grid=filled, words=words, cells=placements)


if __name__ == "__main__":
    import datetime

    ws = generate(datetime.date.today().isoformat())
    print(ws.render())
    print()
    print(ws.render(reveal=True))
    print()
    print(ws.clues_text())
