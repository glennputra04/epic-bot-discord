"""Tiny JSON-file storage for daily questions, answers and scores."""

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
        self.data.setdefault("settings", {})      # guild_id -> {"channel_id": int}
        self.data.setdefault("questions", {})     # day -> question dict
        self.data.setdefault("qotd_answers", {})  # day -> {user_id: option_index}
        self.data.setdefault("crossword", {})     # day -> {user_id: [solved entry keys]}
        self.data.setdefault("scores", {})        # user_id -> {"qotd": int, "crosswords": int}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def score(self, user_id: int) -> dict[str, int]:
        return self.data["scores"].setdefault(str(user_id), {"qotd": 0, "crosswords": 0})
