"""In-memory, per-key rate limiting (token bucket). No Discord code, so it's easy to test.

This protects the bot from users (or a group in one channel) spamming commands or
guesses, which would otherwise mean a JSON save, API calls and public replies per
message. It can't stop network-level DDoS; Discord's gateway sits in front of the bot.
Buckets live in memory only, so a restart resets every limit.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Hashable

log = logging.getLogger(__name__)

# Defaults as (count, seconds). bot.py lets RATELIMIT_* env vars override them.
COMMANDS = (5, 10.0)   # per user, all slash commands
GUESSES = (6, 10.0)    # per user, chat guesses (hangman letters/names, crossword/word search answers)
CHANNEL = (5, 5.0)     # per channel, chat guesses the bot acts on (matches Discord's 5 msgs / 5 s)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    retry_after: float = 0.0  # seconds until the next hit is allowed (0 when allowed)
    notify: bool = False      # True on the first blocked hit since the key's last allowed hit


@dataclass
class _Bucket:
    tokens: float
    updated: float
    warned: bool = False


class RateLimiter:
    """``rate`` hits per ``per`` seconds for each key, refilling continuously.

    ``clock`` is public so tests can swap in a fake one. Buckets idle for ``per``
    seconds are full again, so ``sweep`` drops them without changing any outcome.
    """

    def __init__(self, rate: int, per: float, *, clock: Callable[[], float] = time.monotonic,
                 sweep_every: float = 60.0) -> None:
        if rate < 1 or per <= 0:
            raise ValueError(f"invalid rate limit {rate}/{per}")
        self.rate = rate
        self.per = float(per)
        self.clock = clock
        self.sweep_every = sweep_every
        self._buckets: dict[Hashable, _Bucket] = {}
        self._last_sweep = clock()

    def hit(self, key: Hashable) -> Verdict:
        now = self.clock()
        if now - self._last_sweep >= self.sweep_every:
            self.sweep(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = _Bucket(tokens=float(self.rate), updated=now)
        else:
            elapsed = max(0.0, now - bucket.updated)
            bucket.tokens = min(float(self.rate), bucket.tokens + elapsed * self.rate / self.per)
            bucket.updated = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            bucket.warned = False
            return Verdict(True)
        retry_after = (1 - bucket.tokens) * self.per / self.rate
        notify = not bucket.warned
        bucket.warned = True
        return Verdict(False, retry_after, notify)

    def sweep(self, now: float | None = None) -> int:
        """Drop buckets idle for at least ``per`` seconds. Returns how many were removed."""
        if now is None:
            now = self.clock()
        self._last_sweep = now
        stale = [key for key, b in self._buckets.items() if now - b.updated >= self.per]
        for key in stale:
            del self._buckets[key]
        return len(stale)

    def __len__(self) -> int:
        return len(self._buckets)


def parse_rate(text: str | None, default: tuple[int, float]) -> tuple[int, float]:
    """``"5/10"`` -> ``(5, 10.0)``. Empty means ``default``; invalid logs a warning and uses it."""
    if text is None or not text.strip():
        return default
    try:
        count_text, seconds_text = text.strip().split("/")
        count, seconds = int(count_text), float(seconds_text)
        if count < 1 or not seconds > 0:
            raise ValueError
    except ValueError:
        log.warning("Invalid rate limit %r (expected count/seconds, e.g. 5/10); using %d/%g",
                    text, default[0], default[1])
        return default
    return count, seconds
