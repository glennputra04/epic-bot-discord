"""Tiny JSON-file storage for daily content, solves and scores.

Scores (the leaderboard) are kept in their own file so it can be backed up
or restored independently of the day-to-day bot state.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class Storage:
    def __init__(self, path: str | Path, leaderboard_path: str | Path | None = None):
        self.path = Path(path)
        self.leaderboard_path = (
            Path(leaderboard_path) if leaderboard_path else self.path.with_name("leaderboard.json")
        )
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

        # Leaderboard: user_id -> {"words": int, "crosswords": int, "wordsearches": int}
        legacy_scores = self.data.pop("scores", None)
        if self.leaderboard_path.exists():
            scores = json.loads(self.leaderboard_path.read_text(encoding="utf-8"))
        else:
            # One-time migration: move scores out of the old combined state file.
            scores = legacy_scores or {}
        self.data["scores"] = scores
        if legacy_scores is not None:
            self.save()  # write leaderboard.json and strip scores from state.json

    def save(self) -> None:
        state = {k: v for k, v in self.data.items() if k != "scores"}
        # Write the leaderboard first so a crash never loses migrated scores.
        _write_json(self.leaderboard_path, self.data["scores"])
        _write_json(self.path, state)

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
