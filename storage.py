"""Tiny JSON-file storage for daily content, solves and scores."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

# Daily features that each have their own channel per guild (/set-daily-<feature>).
DAILY_FEATURES = ("qotd", "crossword", "wordsearch", "aredl")


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = asyncio.Lock()
        self.data: dict[str, Any] = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.data.setdefault("settings", {})       # legacy: guild_id -> {"channel_id": int}, migrated on load
        self.data.setdefault("daily_channels", {})  # guild_id -> {feature: channel_id | None (= off)}
        self.data.setdefault("daily_legacy", {})   # guild_id -> [features still set by the old /setchannel migration]
        self.data.setdefault("questions", {})      # day -> boneitis question dict
        self.data.setdefault("links", {})          # day -> {"crossword": url, "wordsearch": url}
        self.data.setdefault("puzzle_posts", {})   # day -> {message_id: "crossword" | "wordsearch"}
        self.data.setdefault("puzzle_channels", {})  # day -> [channel ids where today's puzzles were posted]
        self.data.setdefault("solves", {})         # day -> {user_id: {"crossword": [...], "wordsearch": [...]}}
        self.data.setdefault("scores", {})         # user_id -> {"words": int, "crosswords": int, "wordsearches": int}
        self.data.setdefault("hangman", {})  # daily AREDL hangman: {"day", "level", "previous", "guilds"}
        if self._migrate_legacy_channels():
            self.save()

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

    # ----------------------------------------------------------------------- #
    # Per-feature daily channels (callers hold the lock and save, as elsewhere)
    # ----------------------------------------------------------------------- #
    def _migrate_legacy_channels(self) -> bool:
        """Move the old shared ``settings[gid]["channel_id"]`` to every feature that guild
        hasn't configured yet. ``channel_id`` is removed, so this only ever runs once."""
        changed = False
        for gid, settings in list(self.data["settings"].items()):
            if not isinstance(settings, dict) or not settings.get("channel_id"):
                continue
            channel_id = int(settings.pop("channel_id"))
            channels = self.data["daily_channels"].setdefault(gid, {})
            legacy = self.data["daily_legacy"].setdefault(gid, [])
            for feature in DAILY_FEATURES:
                if feature not in channels:
                    channels[feature] = channel_id
                    if feature not in legacy:
                        legacy.append(feature)
            if not legacy:
                del self.data["daily_legacy"][gid]
            if not settings:
                del self.data["settings"][gid]
            changed = True
        return changed

    def set_daily_channel(self, guild_id: int, feature: str, channel_id: int | None) -> None:
        """Post ``feature`` for this guild in ``channel_id``, or turn it off with ``None``."""
        if feature not in DAILY_FEATURES:
            raise ValueError(f"unknown daily feature {feature!r}")
        gid = str(guild_id)
        self.data["daily_channels"].setdefault(gid, {})[feature] = channel_id
        legacy = self.data["daily_legacy"].get(gid)
        if legacy is not None:
            if feature in legacy:
                legacy.remove(feature)
            if not legacy:
                del self.data["daily_legacy"][gid]

    def guild_daily_channels(self, guild_id: int) -> dict[str, int | None]:
        """This guild's explicit entries (feature -> channel id, or None when off)."""
        return dict(self.data["daily_channels"].get(str(guild_id), {}))

    def is_explicit(self, guild_id: int, feature: str) -> bool:
        """True when an admin set or unset ``feature`` for this guild with a new command."""
        gid = str(guild_id)
        return (feature in self.data["daily_channels"].get(gid, {})
                and feature not in self.data["daily_legacy"].get(gid, []))

    def daily_targets(self, feature: str, default_channel_id: int | None = None,
                      default_guild_id: int | None = None) -> set[int]:
        """Every channel ``feature`` is posted to: all guilds' configured channels, plus the
        ``DAILY_CHANNEL_ID`` default unless its guild explicitly configured ``feature``.
        An unknown default guild (channel not cached) keeps the default, as before."""
        if feature not in DAILY_FEATURES:
            raise ValueError(f"unknown daily feature {feature!r}")
        ids = {int(ch[feature]) for ch in self.data["daily_channels"].values() if ch.get(feature)}
        if default_channel_id and (default_guild_id is None or not self.is_explicit(default_guild_id, feature)):
            ids.add(default_channel_id)
        return ids

    def prune(self, keep_days: set[str]) -> None:
        """Drop per-day bookkeeping that's no longer needed (scores are kept forever)."""
        for key in ("puzzle_posts", "puzzle_channels", "solves", "links"):
            for day in list(self.data[key]):
                if day not in keep_days:
                    del self.data[key][day]
