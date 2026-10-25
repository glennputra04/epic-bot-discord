"""Tiny JSON-file storage for daily content, solves and scores."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = asyncio.Lock()
        self.data: dict[str, Any] = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.data.setdefault("settings", {})       # guild_id -> {"channel_id": int}
        self.data.setdefault("questions", {})      # day -> boneitis question dict
        self.data.setdefault("links", {})          # day -> {"crossword": url, "wordsearch": url}
        self.data.setdefault("puzzle_posts", {})   # day -> {message_id: "crossword" | "wordsearch"}
        self.data.setdefault("puzzle_channels", {})  # day -> [channel ids where today's puzzles were posted]
        self.data.setdefault("solves", {})         # day -> {user_id: {"crossword": [...], "wordsearch": [...]}}
        self.data.setdefault("scores", {})         # user_id -> {"words": int, "crosswords": int, "wordsearches": int}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def score(self, user_id: int) -> dict[str, int]:
        s = self.data["scores"].setdefault(str(user_id), {})
        for k in ("words", "crosswords", "wordsearches"):
            s.setdefault(k, 0)
        return s

    def solves(self, day: str, user_id: int) -> dict[str, list]:
        s = self.data["solves"].setdefault(day, {}).setdefault(str(user_id), {})
        s.setdefault("crossword", [])
        s.setdefault("wordsearch", [])
        return s

    def prune(self, keep_days: set[str]) -> None:
        """Drop per-day bookkeeping that's no longer needed (scores are kept forever)."""
        for key in ("puzzle_posts", "puzzle_channels", "solves", "links"):
            for day in list(self.data[key]):
                if day not in keep_days:
                    del self.data[key][day]
