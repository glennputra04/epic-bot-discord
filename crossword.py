"""Daily crossword generator.

Builds a small, deterministic crossword for a given date from the word bank in
``words.json``. The same date always produces the same puzzle, so every server
member gets an identical puzzle without having to store it anywhere.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

WORDS_FILE = Path(__file__).with_name("words.json")

ACROSS = "across"
DOWN = "down"


@dataclass
class Entry:
    number: int
    direction: str
    row: int
    col: int
    answer: str
    clue: str

    @property
    def key(self) -> str:
        return f"{self.number}-{self.direction}"

    def cells(self) -> list[tuple[int, int]]:
        dr, dc = (0, 1) if self.direction == ACROSS else (1, 0)
        return [(self.row + dr * i, self.col + dc * i) for i in range(len(self.answer))]


@dataclass
class Crossword:
    day: str
    width: int
    height: int
    grid: list[list[str | None]]  # solution letters, None = black square
    entries: list[Entry] = field(default_factory=list)

    def find(self, number: int, direction: str) -> Entry | None:
        for entry in self.entries:
            if entry.number == number and entry.direction == direction:
                return entry
        return None

    def numbers(self) -> dict[tuple[int, int], int]:
        return {(e.row, e.col): e.number for e in self.entries}

    def render(self, solved_keys: set[str] | None = None, reveal: bool = False) -> str:
        """Render the grid as monospace text for a Discord code block.

        ``solved_keys`` fills in the answers a user has already solved;
        ``reveal`` fills in every answer.
        """
        visible: set[tuple[int, int]] = set()
        for entry in self.entries:
            if reveal or (solved_keys and entry.key in solved_keys):
                visible.update(entry.cells())

        numbers = self.numbers()
        lines = []
        for r in range(self.height):
            row = []
            for c in range(self.width):
                letter = self.grid[r][c]
                if letter is None:
                    row.append("███")
                    continue
                ch = letter if (r, c) in visible else "_"
                num = numbers.get((r, c))
                row.append(f"{num}{ch}".ljust(3) if num else f" {ch} ")
            lines.append("".join(row).rstrip())
        return "\n".join(lines)

    def clues_text(self) -> str:
        parts = []
        for direction in (ACROSS, DOWN):
            items = sorted((e for e in self.entries if e.direction == direction), key=lambda e: e.number)
            parts.append(f"**{direction.title()}**")
            parts.extend(f"`{e.number:>2}.` {e.clue} ({len(e.answer)})" for e in items)
            parts.append("")
        return "\n".join(parts).strip()


def load_words() -> list[tuple[str, str]]:
    data = json.loads(WORDS_FILE.read_text(encoding="utf-8"))
    words = []
    for item in data:
        answer = "".join(ch for ch in item["word"].upper() if ch.isalpha())
        if 3 <= len(answer) <= 10:
            words.append((answer, item["clue"]))
    return words


class _Builder:
    def __init__(self, size: int):
        self.size = size
        self.cells: dict[tuple[int, int], str] = {}
        self.used_dirs: dict[tuple[int, int], set[str]] = {}
        self.placed: list[tuple[str, str, int, int, str]] = []  # answer, clue, row, col, dir

    def _letter(self, r: int, c: int) -> str | None:
        return self.cells.get((r, c))

    def can_place(self, word: str, r: int, c: int, direction: str) -> int:
        """Return the number of crossings if the placement is legal, else -1."""
        dr, dc = (0, 1) if direction == ACROSS else (1, 0)
        end_r, end_c = r + dr * (len(word) - 1), c + dc * (len(word) - 1)
        if min(r, c) < 0 or max(end_r, end_c) >= self.size:
            return -1
        # Squares immediately before and after the word must be empty.
        if self._letter(r - dr, c - dc) or self._letter(end_r + dr, end_c + dc):
            return -1

        crossings = 0
        for i, ch in enumerate(word):
            rr, cc = r + dr * i, c + dc * i
            existing = self._letter(rr, cc)
            if existing:
                if existing != ch or direction in self.used_dirs[(rr, cc)]:
                    return -1
                crossings += 1
            else:
                # Don't touch letters sideways, that would create invalid words.
                if self._letter(rr + dc, cc + dr) or self._letter(rr - dc, cc - dr):
                    return -1
        return crossings

    def place(self, word: str, clue: str, r: int, c: int, direction: str) -> None:
        dr, dc = (0, 1) if direction == ACROSS else (1, 0)
        for i, ch in enumerate(word):
            pos = (r + dr * i, c + dc * i)
            self.cells[pos] = ch
            self.used_dirs.setdefault(pos, set()).add(direction)
        self.placed.append((word, clue, r, c, direction))

    def candidates(self, word: str) -> list[tuple[int, int, int, str]]:
        found = []
        for (rr, cc), letter in self.cells.items():
            for i, ch in enumerate(word):
                if ch != letter:
                    continue
                for direction in (ACROSS, DOWN):
                    r, c = (rr, cc - i) if direction == ACROSS else (rr - i, cc)
                    score = self.can_place(word, r, c, direction)
                    if score > 0:
                        found.append((score, r, c, direction))
        return found


def generate(day: str, target_words: int = 10, size: int = 12, attempts: int = 30) -> Crossword:
    """Generate the crossword for ``day`` (ISO date string)."""
    rng = random.Random(f"crossword-{day}")
    bank = load_words()
    best: _Builder | None = None

    for _ in range(attempts):
        pool = bank[:]
        rng.shuffle(pool)
        builder = _Builder(size)
        first, first_clue = max(pool[:8], key=lambda w: len(w[0]))
        pool.remove((first, first_clue))
        builder.place(first, first_clue, size // 2, (size - len(first)) // 2, ACROSS)

        used = {first}
        for word, clue in pool:
            if len(builder.placed) >= target_words:
                break
            if word in used:
                continue
            options = builder.candidates(word)
            if not options:
                continue
            top = max(o[0] for o in options)
            _, r, c, direction = rng.choice([o for o in options if o[0] == top])
            builder.place(word, clue, r, c, direction)
            used.add(word)

        if best is None or len(builder.placed) > len(best.placed):
            best = builder
        if len(best.placed) >= target_words:
            break

    assert best is not None
    return _finalize(day, best)


def _finalize(day: str, builder: _Builder) -> Crossword:
    rows = [r for r, _ in builder.cells]
    cols = [c for _, c in builder.cells]
    top, left = min(rows), min(cols)
    height, width = max(rows) - top + 1, max(cols) - left + 1

    grid: list[list[str | None]] = [[None] * width for _ in range(height)]
    for (r, c), ch in builder.cells.items():
        grid[r - top][c - left] = ch

    # Number the starting squares in reading order, like a real crossword.
    starts = sorted({(r - top, c - left) for _, _, r, c, _ in builder.placed})
    numbering = {pos: i + 1 for i, pos in enumerate(starts)}

    entries = [
        Entry(numbering[(r - top, c - left)], d, r - top, c - left, word, clue)
        for word, clue, r, c, d in builder.placed
    ]
    entries.sort(key=lambda e: (e.direction, e.number))
    return Crossword(day=day, width=width, height=height, grid=grid, entries=entries)


if __name__ == "__main__":
    import datetime

    puzzle = generate(datetime.date.today().isoformat())
    print(puzzle.render())
    print()
    print(puzzle.render(reveal=True))
    print()
    print(puzzle.clues_text())
