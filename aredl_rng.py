"""AREDL RNG: a random AREDL level spawns every hour and the first player to type
``collect`` adds it to their inventory (no Discord code, so it's easy to test).

Higher-ranked levels are rarer. Each level's spawn weight is ``p * CURVE / (CURVE + p)``
for list position ``p``: roughly proportional to ``p`` near the top (so #1 is ~60x rarer
than #100) and flattening further down (so #1000 is still a bit rarer than #1500).
"""

from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass

COLLECT_WORD = "collect"
SPAWN_LIFETIME = dt.timedelta(hours=1)  # a spawn can be collected until the next one
CURVE = 150                             # higher = the top of the list gets rarer still
PAGE_SIZE = 15                          # inventory lines per page


@dataclass(frozen=True)
class Rarity:
    key: str
    name: str
    emoji: str
    color: int
    max_position: int | None  # last list position in this tier (None = everything below)


# Rarest first.
RARITIES = (
    Rarity("mythical", "Mythical", "🌌", 0xFF2E88, 10),
    Rarity("legendary", "Legendary", "🌟", 0xFFB300, 50),
    Rarity("epic", "Epic", "💜", 0x9B59B6, 150),
    Rarity("rare", "Rare", "💙", 0x3498DB, 400),
    Rarity("uncommon", "Uncommon", "💚", 0x2ECC71, 800),
    Rarity("common", "Common", "⚪", 0x95A5A6, None),
)
BY_KEY = {r.key: r for r in RARITIES}


def rarity_for(position: int) -> Rarity:
    for rarity in RARITIES:
        if rarity.max_position is None or position <= rarity.max_position:
            return rarity
    return RARITIES[-1]


def weight(position: int) -> float:
    return position * CURVE / (CURVE + position)


def spawnable(levels: list[dict]) -> list[dict]:
    """Ranked main-list levels only (legacy, pending and removed levels don't spawn)."""
    return [
        lv for lv in levels
        if isinstance(lv.get("position"), int) and lv["position"] >= 1
        and lv.get("status") not in ("Legacy", "Pending", "Removed")
    ]


def card(level: dict, chance: float) -> dict:
    """The fields shown on a spawn, saved so the spawn survives restarts and list changes."""
    return {
        "id": level["id"],
        "name": level["name"],
        "position": level["position"],
        "rarity": rarity_for(level["position"]).key,
        "chance": chance,
        "level_id": level.get("level_id"),
        "two_player": bool(level.get("two_player")),
        "tags": [t for t in (level.get("tags") or []) if isinstance(t, str)],
        "edel_enjoyment": level.get("edel_enjoyment"),
        "gddl_tier": level.get("gddl_tier"),
        "nlw_tier": level.get("nlw_tier"),
    }


def pick(levels: list[dict], rng: random.Random | None = None) -> dict | None:
    """A random spawnable level as a card, weighted so higher ranks are rarer."""
    pool = spawnable(levels)
    if not pool:
        return None
    weights = [weight(lv["position"]) for lv in pool]
    index = (rng or random).choices(range(len(pool)), weights)[0]
    return card(pool[index], weights[index] / sum(weights))


def tier_odds(levels: list[dict]) -> dict[str, float]:
    """Chance per spawn of each rarity, for the current list."""
    pool = spawnable(levels)
    total = sum(weight(lv["position"]) for lv in pool) or 1.0
    odds = {r.key: 0.0 for r in RARITIES}
    for lv in pool:
        odds[rarity_for(lv["position"]).key] += weight(lv["position"]) / total
    return odds


def one_in(chance: float) -> str:
    """``0.0004`` -> ``1 in 2,500``."""
    return f"1 in {max(1, round(1 / chance)):,}" if chance > 0 else "—"


_YOUTUBE_ID = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|embed/|live/))([\w-]{11})")


def youtube_thumbnail(url: str | None) -> str | None:
    match = _YOUTUBE_ID.search(url or "")
    return f"https://img.youtube.com/vi/{match.group(1)}/hqdefault.jpg" if match else None


# --------------------------------------------------------------------------- #
# Spawns and inventories (callers hold the storage lock and save)
# --------------------------------------------------------------------------- #
def is_collect(text: str) -> bool:
    return text.strip().lower().rstrip("!.") == COLLECT_WORD


def is_open(spawn: dict | None, now: dt.datetime) -> bool:
    """Unclaimed and not older than SPAWN_LIFETIME (e.g. left over from before a restart)."""
    if not spawn or spawn.get("claimed_by"):
        return False
    try:
        spawned = dt.datetime.fromisoformat(spawn["spawned_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return now - spawned < SPAWN_LIFETIME


def add_to_inventory(inventory: dict, level: dict, when: str) -> int:
    """Add one copy of ``level`` (a card) and return how many the player now has."""
    entry = inventory.setdefault(level["id"], {"count": 0, "first_collected": when})
    entry.update(name=level["name"], position=level["position"], rarity=level["rarity"])
    entry["count"] += 1
    return entry["count"]


def sorted_inventory(inventory: dict) -> list[dict]:
    """Inventory entries, best-ranked first."""
    return sorted(inventory.values(), key=lambda e: (e.get("position") or 10**9, e.get("name", "")))


def rarity_counts(inventory: dict) -> dict[str, int]:
    """Unique levels per rarity."""
    counts = {r.key: 0 for r in RARITIES}
    for entry in inventory.values():
        if entry.get("rarity") in counts:
            counts[entry["rarity"]] += 1
    return counts
