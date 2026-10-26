"""Daily AREDL hangman (no Discord code, so it's easy to test).

Every UTC day picks one level from the AREDL list (https://api.aredl.net/v2/docs).
Players guess its name letter by letter, or all at once, using the level's
description as a hint. Each player has their own lives.

The day boundary is 00:00 UTC from the system clock (``datetime.now(timezone.utc)``).
Servers keep their clock NTP-synced, so an external time API would only add a
failure mode. The bot's ``TIMEZONE`` setting is only for the other daily posts.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import time
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

API_URL = "https://api.aredl.net/v2/api/aredl/levels"
LIVES = 6                    # lives per player per day
CACHE_TTL = 6 * 60 * 60      # seconds to keep the downloaded level list
NO_DESCRIPTION = "No description available."
REDACTED = "███"
MAX_MESSAGES = 50            # board message ids remembered per guild (for replies)

# A puzzle stays PLAYING until someone solves it or the UTC day rolls over; running out
# of lives only locks out that player. LOST is kept so older saved state still loads.
PLAYING, WON, LOST = "playing", "won", "lost"

# Guess outcomes
INVALID = "invalid"      # nothing to guess (e.g. only punctuation)
FINISHED = "finished"    # today's puzzle is already solved
OUT = "out"              # the guesser has no lives left
REPEAT = "repeat"        # already guessed; no life lost
HIT = "hit"              # letter is in the name
MISS = "miss"            # wrong letter or name; one life lost
WIN = "win"              # the guess completed the name

# Coarse list-position hint shown while playing (the exact #N would give the name away).
# Main-list positions above the last limit fall into "#1001+".
POSITION_BANDS = (100, 250, 500, 1000)


# --------------------------------------------------------------------------- #
# Clock (UTC, system clock)
# --------------------------------------------------------------------------- #
def utc_now(now: dt.datetime | None = None) -> dt.datetime:
    return now.astimezone(dt.timezone.utc) if now is not None else dt.datetime.now(dt.timezone.utc)


def utc_today(now: dt.datetime | None = None) -> str:
    return utc_now(now).date().isoformat()


def next_reset(now: dt.datetime | None = None) -> dt.datetime:
    """The next 00:00 UTC, when a new level starts."""
    tomorrow = utc_now(now).date() + dt.timedelta(days=1)
    return dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# AREDL API
# --------------------------------------------------------------------------- #
_cache: list[dict] = []
_cache_time = 0.0


async def _download() -> object:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(API_URL) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            return await resp.json(content_type=None)


async def fetch_levels() -> list[dict]:
    """Return the AREDL level list, cached for ``CACHE_TTL``. On failure, return the
    stale cache (or ``[]`` if there's none) after logging a warning."""
    global _cache, _cache_time
    if _cache and time.monotonic() - _cache_time < CACHE_TTL:
        return _cache
    for attempt in range(2):
        try:
            data = await _download()
            if not isinstance(data, list):
                raise ValueError(f"expected a list, got {type(data).__name__}")
            levels = [
                lv for lv in data
                if isinstance(lv, dict) and isinstance(lv.get("id"), str)
                and isinstance(lv.get("name"), str) and any(ch.isalnum() for ch in lv["name"])
            ]
            if not levels:
                raise ValueError("no usable levels in response")
            _cache, _cache_time = levels, time.monotonic()
            return _cache
        except Exception as exc:
            log.warning("AREDL request failed (attempt %d/2): %s", attempt + 1, exc)
    return _cache


# --------------------------------------------------------------------------- #
# Daily level
# --------------------------------------------------------------------------- #
def snapshot(level: dict) -> dict:
    """The fields the game needs, stored so the level survives restarts and list changes."""
    return {
        "id": level["id"],
        "name": level["name"],
        "position": level.get("position"),
        "status": level.get("status"),
        "description": level.get("description"),
        "tags": list(level.get("tags") or []),
    }


def pick_level(day: str, levels: list[dict]) -> dict:
    """Same day = same level, whatever order the API returns (sorted by stable UUID)."""
    ordered = sorted(levels, key=lambda lv: lv["id"])
    index = int(hashlib.sha256(day.encode()).hexdigest(), 16) % len(ordered)
    return snapshot(ordered[index])


def ensure_day(data: dict, day: str, levels: list[dict] | None) -> bool:
    """Start ``day``'s puzzle in ``data`` (storage's "hangman" dict) if it isn't already.

    Returns True when ``data`` changed. Needs ``levels`` only when the day changes;
    with no levels the old state is left untouched.
    """
    if data.get("day") == day:
        return False
    if not levels:
        return False
    old = data.get("level")
    if old:
        data["previous"] = {"day": data.get("day"), "name": old["name"], "position": old.get("position")}
    data["day"] = day
    data["level"] = pick_level(day, levels)
    data["guilds"] = {}
    return True


def current(data: dict, day: str) -> bool:
    return data.get("day") == day and "level" in data


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def normalize(text: str) -> str:
    """Lower-case letters and digits only, so case, spaces and punctuation don't matter."""
    return "".join(ch for ch in text.replace("||", "").lower() if ch.isalnum())


def is_letter(text: str) -> bool:
    text = text.replace("||", "").strip()
    return len(text) == 1 and text.isalnum()


def mask(name: str, guessed: set[str], reveal: bool = False) -> str:
    """``Artemis 13`` with a, 3 guessed -> ``A _ _ _ _ _ _  /  _ 3``. Punctuation is shown."""
    def cell(ch: str) -> str:
        if not ch.isalnum() or reveal or ch.lower() in guessed:
            return ch
        return "_"

    return "  /  ".join(" ".join(cell(ch) for ch in word) for word in name.split())


def redact(text: str, name: str) -> str:
    """Hide the level name (and its 4+ character words) in a description.

    Terms of 4+ letters/digits are hidden inside other words too (``#RateFragile``,
    ``pistons``); shorter ones only as whole words, so ``Io`` doesn't eat ``radio``.
    """
    terms = {name.strip()} | {t for t in re.findall(r"\w+", name) if len(t) >= 4}
    for term in sorted(terms, key=len, reverse=True):
        pattern = re.escape(term)
        if len(normalize(term)) < 4:
            pattern = rf"(?<!\w){pattern}(?!\w)"
        text = re.sub(pattern, REDACTED, text, flags=re.IGNORECASE)
    return text


def position_band(position: int | None, status: str | None = None) -> str:
    """``5`` -> ``Top 100``, ``300`` -> ``#251–500``, Legacy levels -> ``Legacy``."""
    if status == "Legacy":
        return "Legacy"
    if not isinstance(position, int):
        return "—"
    low = 1
    for limit in POSITION_BANDS:
        if position <= limit:
            return f"Top {limit}" if low == 1 else f"#{low}–{limit}"
        low = limit + 1
    return f"#{low}+"


# --------------------------------------------------------------------------- #
# Game
# --------------------------------------------------------------------------- #
@dataclass
class GuessResult:
    outcome: str
    kind: str   # "letter" or "name"
    value: str  # upper-cased letter, or the raw guess (max 100 chars)
    count: int = 0
    lives: int = LIVES


class Game:
    """One guild's board for today's level. ``state`` is the stored dict, mutated in place."""

    def __init__(self, level: dict, state: dict):
        self.level = level
        self.state = state
        for key in ("guessed", "wrong", "wrong_names", "messages", "channels"):
            state.setdefault(key, [])
        state.setdefault("lives", {})
        state.setdefault("status", PLAYING)
        state.setdefault("winner", None)

    @property
    def name(self) -> str:
        return self.level["name"]

    @property
    def position(self) -> int | None:
        return self.level.get("position")

    @property
    def tags(self) -> list[str]:
        return list(self.level.get("tags") or [])

    @property
    def finished(self) -> bool:
        return self.state["status"] != PLAYING

    def board(self) -> str:
        return mask(self.name, set(self.state["guessed"]), reveal=self.finished)

    def hint(self) -> str | None:
        """The description (name redacted while playing), or None if there isn't one."""
        desc = self.level.get("description")
        if not isinstance(desc, str) or not desc.strip():
            return None
        return desc.strip() if self.finished else redact(desc.strip(), self.name)

    def players(self) -> dict[int, int]:
        return {int(uid): n for uid, n in self.state["lives"].items()}

    def lives_left(self, user_id: int) -> int:
        return self.state["lives"].get(str(user_id), LIVES)

    def solved(self) -> bool:
        return all(ch in self.state["guessed"] for ch in normalize(self.name))

    def remember_message(self, message_id: int, channel_id: int) -> None:
        self.state["messages"] = (self.state["messages"] + [message_id])[-MAX_MESSAGES:]
        if channel_id not in self.state["channels"]:
            self.state["channels"].append(channel_id)

    def _miss(self, user_id: int) -> str:
        # Only the guesser pays; the shared puzzle stays open for everyone else.
        self.state["lives"][str(user_id)] -= 1
        return MISS

    def _win(self, user_id: int) -> str:
        self.state["status"] = WON
        self.state["winner"] = user_id
        return WIN

    def guess(self, user_id: int, text: str) -> GuessResult:
        norm = normalize(text)
        kind = "letter" if len(norm) == 1 else "name"
        value = norm.upper() if kind == "letter" else text.replace("||", "").strip()[:100]

        def result(outcome: str, count: int = 0) -> GuessResult:
            return GuessResult(outcome, kind, value, count, self.lives_left(user_id))

        if self.finished:
            return result(FINISHED)
        if not norm:
            return result(INVALID)
        if self.lives_left(user_id) <= 0:
            return result(OUT)
        self.state["lives"].setdefault(str(user_id), LIVES)

        if kind == "letter":
            if norm in self.state["guessed"]:
                return result(REPEAT)
            self.state["guessed"].append(norm)
            count = self.name.lower().count(norm)
            if count:
                return result(self._win(user_id) if self.solved() else HIT, count)
            self.state["wrong"].append(norm)
            return result(self._miss(user_id))

        if norm == normalize(self.name):
            for ch in norm:
                if ch not in self.state["guessed"]:
                    self.state["guessed"].append(ch)
            return result(self._win(user_id))
        if norm in self.state["wrong_names"]:
            return result(REPEAT)
        self.state["wrong_names"].append(norm)
        return result(self._miss(user_id))

    def give_up(self, user_id: int) -> bool:
        """Put the player out of lives for today. Personal only: it never ends the
        shared puzzle, so a peek right after the daily post can't spoil it for others."""
        if self.finished:
            return False
        self.state["lives"][str(user_id)] = 0
        return True


def game_for(data: dict, guild_id: int) -> Game:
    return Game(data["level"], data["guilds"].setdefault(str(guild_id), {}))


if __name__ == "__main__":
    import asyncio

    day = utc_today()
    levels = asyncio.run(fetch_levels())
    if not levels:
        raise SystemExit("Couldn't reach the AREDL API.")
    preview = Game(pick_level(day, levels), {})
    print(f"{day} (UTC)")
    print(preview.board())
    print(preview.hint() or NO_DESCRIPTION)
