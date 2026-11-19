# Implementation Plan: Daily AREDL Hangman

Project root (all paths below are relative to it): `c:\Users\Hp\OneDrive\Dokumen\VerdantBot\epic-bot-discord`.
Python: `.venv\Scripts\python.exe` (3.12). Production is Python 3.11 on Wispbyte.

Hard rules for the implementer:
- Do NOT git commit or push. Do NOT run `bot.py` / connect to Discord.
- Do NOT add dependencies (requirements.txt stays as is). HTTP via `aiohttp`. Tests use stdlib `unittest` only.
- Never print, echo or log values from `.env` (it holds the real token).
- Python 3.11 compatible: no PEP 695 generics/`type` statements, no PEP 701 f-strings (no reusing the same quote inside an f-string `{}`), no `typing.override`. Keep `from __future__ import annotations` like the other modules.

## Confirmed API shape (fetched live)

`GET https://api.aredl.net/v2/api/aredl/levels` → HTTP 200, `application/json`, ~890 KB, a **plain JSON list** (not paginated) of 1643 objects. Every object has exactly these keys:
`id` (UUID str, stable), `name` (str), `position` (int, 1..1643), `publisher_id` (UUID), `points` (int), `status` (`"MainList"` for positions 1..1609, `"Legacy"` for 1610..1643), `requires_raw_footage`, `level_id` (GD id), `two_player` (bool), `tags` (list[str], e.g. `"2.2"`, `"Long"`, `"Wave"`, `"NONG"`; 2 levels have `[]`), `description` (str or null), `song`, `edel_enjoyment`, `is_edel_pending`, `gddl_tier`, `nlw_tier`.

Findings that drive the design:
- `description`: 762 are `null`, 47 are `""`/whitespace → 809/1643 have no usable description.
- **39 descriptions contain the level's own name** (e.g. NOMAD, The Golden, Trueffet, cognitohazard) → the hint MUST be redacted.
- There are **no `creators`/`verifier` fields** in this response (only `publisher_id` UUID). Do not fetch extra endpoints for them; the fallback hint uses `tags` instead.
- Names: 2..28 chars, only non-alphanumeric chars are space, `(` and `)` (e.g. `Deimos (ItsHybrid)`, `Codependence (Solo)`); 96 contain digits (`Artemis 13`, `X84`, `IH8`); mixed case. No duplicate names.

## Decisions (with rationale)

1. **Daily, deterministic puzzle (new requirement).** Today's level = `sorted(levels, key=id)[int(sha256(day.encode()).hexdigest(), 16) % len(levels)]` where `day` is the UTC date `YYYY-MM-DD`. Sorting by `id` (UUID) is stable when positions shift; never use `hash()` or unseeded `random`.
2. **UTC day boundary from the system clock.** `datetime.now(timezone.utc)`; never the `TIMEZONE` env var (that stays for QOTD/crossword). No HTTP time API: servers are NTP-synced and an external API adds a failure mode for no gain. Document this in a comment and the README.
3. **Persist via storage.py** under a new `storage.data["hangman"]` key: today's day, a snapshot of the chosen level, the previous day's answer, and per-guild progress. Rationale: the level must survive restarts and mid-day cache refreshes, and a finished puzzle must stay finished after a restart (in-memory state would let people re-play after a loss).
4. **Scope: one shared daily level; progress per guild.** Each guild has one board per UTC day (revealed letters, wrong letters, per-user lives, status). Per-guild (not per-channel) stops channel-hopping to replay the same daily level; the board can be shown in any channel of the guild. This replaces "one active game per channel". Slash commands are `guild_only`.
5. **Lives per user, per guild, per day**: `LIVES = 6`. A wrong letter or wrong full-name guess costs only the guesser a life; at 0 they can't guess until the next UTC day. Repeated letters and repeated wrong names cost nothing.
6. **Finish rules**: `won` when the name is fully revealed or guessed; `lost` when every user who has guessed in that guild is at 0 lives (the rule from the brief; trade-off noted: an early solo player can end the guild's day, tune `LIVES` if that's too harsh). Finished stays finished until the next UTC day: `/hangman start` only shows today's board, it never re-rolls.
7. **Input**: slash group `/hangman start` (show today's board), `/hangman guess <guess>`, `/hangman giveup`; plus plain messages to match the existing games (which read plain answers with `message_content` intent, already enabled in `DailyBot.__init__`): in a channel where today's board was posted for that guild, a message that is exactly one letter/digit is a letter guess; a message that *replies to a hangman board message* is a guess (letter if 1 char, else full name). Single characters never match `answers.parse` (needs a number), so no clash with crossword answers.
8. **Give up is personal**: `/hangman giveup` sets the caller's lives to 0 and shows them the answer ephemerally. It does not end the guild's puzzle (unless that makes everyone who played out of lives → `lost`, which is then announced publicly). No one can stop the shared daily puzzle for others.
9. **Rollover auto-post**: new `tasks.loop(time=dt.time(0, 0, 30, tzinfo=dt.timezone.utc))` posts the new board to `daily_channel_ids()` (same channels/pattern as `daily_post`), with yesterday's answer in the note. 30 s after midnight guards against the loop waking a hair early and computing yesterday's date. Interactions also reset lazily (any command checks the date first), so a missed post or failed API call never blocks play.
10. **No inactivity timeout**: games last exactly one UTC day, so a timeout is unnecessary.
11. **Hint**: redacted description; if null/empty → `"No description available."` plus a `Tags` field (non-spoiler). `Position` (`#N`, plus `• Legacy` when status is Legacy) is always shown. Never the name. Redaction replaces, case-insensitively at word boundaries, the full name and each name token of ≥ 4 alphanumeric chars with `███`.
12. **Masking**: each alphanumeric char → `_` (or the revealed char, original case), chars within a word separated by one space, words separated by `  /  `, punctuation shown as-is. Rendered inside a ```` ``` ```` code block so Discord markdown can't eat underscores. Example: `Artemis 13` with `a`,`3` guessed → `A _ _ _ _ _ _  /  _ 3`; `Codependence (Solo)` → `_ _ _ _ _ _ _ _ _ _ _ _  /  ( _ _ _ _ )`.
13. **Normalization**: `"".join(ch for ch in text.lower() if ch.isalnum())` after removing `||` spoiler bars. Digits are kept (unlike `answers.normalize`), so `artemis-13` matches `Artemis 13`. Use `.lower()` not `.casefold()` to keep chars 1:1.
14. **Levels cache**: in-memory, `CACHE_TTL = 6 * 60 * 60` s using `time.monotonic()`. On failure log a warning (like `qotd.py`) and return the stale cache (or `[]`). Only needed when a new day must be chosen, because today's level is snapshotted in storage.

## Storage shape (`storage.data["hangman"]`)

```json
{
  "day": "2026-10-25",
  "level": {"id": "...", "name": "GRIEF", "position": 1, "status": "MainList", "description": "... or null", "tags": ["2.2", "XL"]},
  "previous": {"day": "2026-10-24", "name": "...", "position": 12},
  "guilds": {
    "<guild_id>": {
      "guessed": ["e", "3"], "wrong": ["z"], "wrong_names": ["grief"],
      "lives": {"<user_id>": 5}, "status": "playing", "winner": null,
      "messages": [123], "channels": [456]
    }
  }
}
```

## Acceptance criteria (coder and reviewer must both enforce)

- AC1 Same UTC date → same level for everyone, regardless of the input list order (sorted by `id`); different dates usually give different levels; selection uses `hashlib.sha256` of the `YYYY-MM-DD` string, never `hash()`/unseeded `random`.
- AC2 The chosen level snapshot is stored in `DATA_FILE` via storage.py; calling the day logic again on the same date with a different/refreshed levels list keeps the same level and progress.
- AC3 When the UTC date changes: new level, `guilds` cleared (fresh lives, no guesses, status playing), and `previous` holds the old answer. A `won`/`lost` puzzle stays finished until then; `start` never re-rolls.
- AC4 The hangman day is computed with `datetime.now(timezone.utc)` (injectable `now` for tests), never `TIMEZONE`.
- AC5 The masked board never shows unrevealed letters; an in-progress embed never contains the level name anywhere (title, description, fields, footer), even when the API description contains it.
- AC6 Null/empty description → "No description available." plus tags; position always shown.
- AC7 Per-user lives (default 6): wrong letter/name costs only the guesser; 0 lives blocks further guesses; repeats cost nothing and say so; replies show the guesser's remaining lives.
- AC8 Letter guesses are case-insensitive and reveal all occurrences; full-name guesses are case/space/punctuation-insensitive.
- AC9 API failure → user-facing error message, logged warning, no crash.
- AC10 Python 3.11 compatible; no new dependencies; README updated.

---

- [ ] 1. Add the `hangman` key to storage.
      In `Storage.__init__` add `self.data.setdefault("hangman", {})  # daily AREDL hangman: {"day", "level", "previous", "guilds"}` after the `scores` line. Do not add it to `prune()` (hangman manages its own day).
      Files: `storage.py`
      Verify: `.venv\Scripts\python.exe -c "import tempfile, os; from storage import Storage; s = Storage(os.path.join(tempfile.mkdtemp(), 's.json')); print(s.data['hangman'])"` prints `{}`.

- [ ] 2. Create `hangman.py` with all game logic (no Discord code, like `game.py`) and the AREDL client (like `qotd.py`).
      Module docstring describing daily AREDL hangman and the UTC/system-clock decision. Contents:
      - Constants: `API_URL = "https://api.aredl.net/v2/api/aredl/levels"`, `LIVES = 6`, `CACHE_TTL = 6 * 60 * 60`, `NO_DESCRIPTION = "No description available."`, `REDACTED = "███"`, `MAX_MESSAGES = 50`, status strings `PLAYING/WON/LOST = "playing"/"won"/"lost"`, outcome strings `INVALID, FINISHED, OUT, REPEAT, HIT, MISS, WIN, LOST_GAME` (`"lost"` outcome = a miss that ended the game). `log = logging.getLogger(__name__)`.
      - Clock: `utc_now(now: dt.datetime | None = None) -> dt.datetime` (returns `now.astimezone(dt.timezone.utc)` if given, else `dt.datetime.now(dt.timezone.utc)`), `utc_today(now=None) -> str` (ISO date of `utc_now(now)`), `next_reset(now=None) -> dt.datetime` (next 00:00 UTC).
      - HTTP: module globals `_cache: list[dict] = []`, `_cache_time = 0.0`. `async def _download() -> object` does one `aiohttp` GET with `ClientTimeout(total=20)`, raises `ValueError(f"HTTP {status}")` on non-200, returns `await resp.json(content_type=None)`. `async def fetch_levels() -> list[dict]`: return `_cache` if non-empty and `time.monotonic() - _cache_time < CACHE_TTL`; else up to 2 attempts of `_download()`, require a list, keep items that are dicts with a str `id` and a str `name` containing at least one alnum char, raise if none remain; on success set the cache; on exception `log.warning("AREDL request failed (attempt %d/2): %s", ...)`; after failures return `_cache` (stale or `[]`).
      - Selection: `snapshot(level: dict) -> dict` keeps `id, name, position, status, description, tags` (tags default `[]`). `pick_level(day: str, levels: list[dict]) -> dict` = `snapshot(sorted(levels, key=lambda lv: lv["id"])[int(hashlib.sha256(day.encode()).hexdigest(), 16) % len(levels)])`.
      - Day state: `ensure_day(data: dict, day: str, levels: list[dict] | None) -> bool` — if `data.get("day") == day` return False (no change, levels ignored). Otherwise require non-empty `levels` (return False and leave data untouched if empty/None), set `data["previous"] = {"day", "name", "position"}` from the old level if one existed, then `data["day"] = day`, `data["level"] = pick_level(day, levels)`, `data["guilds"] = {}`; return True. `current(data, day) -> bool` = `data.get("day") == day and "level" in data`.
      - Text helpers: `normalize(text)` (strip `||`, lower, keep `isalnum`), `is_letter(text)` (after stripping `||` and whitespace: exactly 1 char and `isalnum()`), `mask(name, guessed: set[str], reveal=False) -> str` per Decision 12 (split on whitespace), `redact(text, name) -> str` per Decision 11 using `re.sub(rf"(?<!\w){re.escape(term)}(?!\w)", REDACTED, text, flags=re.IGNORECASE)` over the full name and tokens from `re.findall(r"\w+", name)` with `len >= 4`, longest first.
      - `class Game`: `__init__(self, level: dict, state: dict)` keeps references (state is the dict stored in `data["guilds"][guild_id]`, mutated in place) and `setdefault`s `guessed, wrong, wrong_names` (lists), `lives` (dict str→int), `status` (`PLAYING`), `winner` (None), `messages`, `channels` (lists). Properties/methods: `name`, `position`, `finished` (status != PLAYING), `board()` (`mask(name, set(guessed), reveal=finished)`), `hint() -> str | None` (redacted description, or None if null/blank; unredacted when finished), `tags`, `players() -> dict[int, int]`, `lives_left(user_id) -> int` (default `LIVES`), `solved()`, `remember_message(message_id, channel_id)` (append ids, dedupe channels, keep last `MAX_MESSAGES` message ids).
      - `GuessResult` dataclass: `outcome: str`, `kind: str` (`"letter"`/`"name"`), `value: str` (upper-cased letter or the raw stripped guess truncated to 100 chars), `count: int = 0`, `lives: int = LIVES`.
      - `Game.guess(user_id: int, text: str) -> GuessResult`: order of checks: finished → `FINISHED`; `normalize(text)` empty → `INVALID`; guesser lives ≤ 0 → `OUT`; then register the guesser (`lives.setdefault(str(uid), LIVES)`). Letter (normalized length 1): already in `guessed` → `REPEAT`; else append; count = occurrences in `name.lower()`; count > 0 → `WIN` (set status WON, winner) if `solved()` else `HIT`; count 0 → append to `wrong`, lives −1, `LOST_GAME` (status LOST) if every registered player ≤ 0 else `MISS`. Name (length > 1): equals `normalize(name)` → add all name chars to `guessed`, status WON, winner, `WIN`; in `wrong_names` → `REPEAT`; else append to `wrong_names`, lives −1, `LOST_GAME`/`MISS` as above. Every result carries the guesser's lives after the guess.
      - `Game.give_up(user_id) -> bool`: if finished return False; set that user's lives to 0 (registering them); if every player ≤ 0 set status LOST; return True.
      - `game_for(data: dict, guild_id: int) -> Game` = `Game(data["level"], data["guilds"].setdefault(str(guild_id), {}))`.
      - `if __name__ == "__main__":` print today's UTC day, the masked board and hint for today's pick (fetches live), mirroring `crossword.py`'s preview block.
      Files: `hangman.py`
      Verify: `.venv\Scripts\python.exe -m py_compile hangman.py` succeeds; `py -3.10 -m py_compile hangman.py` succeeds (stricter than 3.11 for syntax, catches 3.12-only f-strings); `.venv\Scripts\python.exe hangman.py` prints a UTC date and a masked board without the name.

- [ ] 3. Add unit tests for `hangman.py` with stdlib `unittest` (no test framework exists; don't add pytest).
      Create `tests\__init__.py` (empty) and `tests\test_hangman.py`. Insert the project root on `sys.path` at the top of the test file. Use small fake level dicts (`id`, `name`, `position`, `status`, `description`, `tags`). Cover:
      - `mask`: `Artemis 13` → all underscores with `  /  ` word gap; with `{"a","3"}` → `A _ _ _ _ _ _  /  _ 3`; parens visible for `Codependence (Solo)`; `reveal=True` shows the name.
      - `normalize`/`is_letter`: `"Artemis-13!"` → `"artemis13"`; `"||e||"` is a letter; `"ab"`, `""`, `"!"` are not.
      - `redact`: description containing `NOMAD`/`Nomad` and `The Golden` → name and ≥4-char tokens replaced, `"The"` elsewhere untouched.
      - Letter guesses: case-insensitive hit reveals all occurrences (count), miss costs only that user a life, other users still at 6, repeat costs nothing (`REPEAT`), user at 0 → `OUT`.
      - Name guesses: `"artemis-13"` wins and sets winner; wrong name costs a life; same wrong name again is `REPEAT`; guessing letters that complete the name → `WIN`.
      - All players out → `LOST_GAME`, status lost, further guesses → `FINISHED`; `give_up` sets lives 0, ends the game only if all players are out.
      - Determinism (AC1): `pick_level("2026-01-01", levels)` equal on repeated calls and on a shuffled copy; across 30 consecutive dates with 100 fake levels, ≥ 15 distinct picks; matches an index computed independently with `hashlib.sha256`.
      - Day reset (AC2/AC3): `ensure_day(data, d1, levels)` → True; make guesses via `game_for`; `ensure_day(data, d1, other_shuffled_levels)` → False and level/progress unchanged; `ensure_day(data, d2, levels)` → True, new day, `guilds == {}`, fresh `lives_left == LIVES`, `previous["name"]` is the old name; `ensure_day(data, d3, [])` → False and data unchanged.
      - Clock (AC4): `utc_today(datetime(2026,1,1,23,59,59,tzinfo=utc))` = `"2026-01-01"`, `+1s` = `"2026-01-02"`; an aware non-UTC datetime (e.g. `timezone(timedelta(hours=7))` 2026-01-02 03:00) → `"2026-01-01"`; `next_reset` returns the next midnight UTC.
      - `fetch_levels` (`unittest.IsolatedAsyncioTestCase`, patch `hangman._download` with `unittest.mock.AsyncMock`, reset `hangman._cache = []` and `hangman._cache_time = 0.0` in `setUp`): filters invalid entries; second call within TTL doesn't download again; after forcing `_cache_time` old and making `_download` raise, returns the stale cache and logs a warning (`assertLogs("hangman", "WARNING")`); with no cache and failures returns `[]`.
      Files: `tests\__init__.py`, `tests\test_hangman.py`
      Verify: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — all tests pass.

- [ ] 4. Integrate hangman into `bot.py` (depends on 2).
      Changes, matching existing style and section comments:
      - Imports: `import hangman  # noqa: E402` in the local-imports block (alphabetical, after `game`). Update the module docstring to mention Daily AREDL Hangman. Add `hangman_lock = asyncio.Lock()` next to `puzzel_lock`.
      - Daily content section: `async def hangman_ready() -> bool` — `day = hangman.utc_today()`; if `hangman.current(storage.data["hangman"], day)` return True; `async with hangman_lock:` re-check, `levels = await hangman.fetch_levels()`, then `async with storage.lock:` `changed = hangman.ensure_day(storage.data["hangman"], day, levels)`, `storage.save()` if changed; return `hangman.current(...)`. If it returns False, log `log.warning("No AREDL levels available for hangman %s", day)`.
      - Embeds section: `def hangman_embed(g: hangman.Game, note: str | None = None) -> discord.Embed`. Title `f"🎯 Daily AREDL Hangman — {storage.data['hangman']['day']} (UTC)"` (use single quotes inside the f-string for 3.11). Description: optional `note` + blank line + ```` f"```\n{g.board()}\n```" ````. Fields: `Hint` = `g.hint()` or `hangman.NO_DESCRIPTION` (truncate to 1024); if no description and tags → `Tags` = `", ".join(tags)`; `Position` inline = `#N` (+ ` • Legacy`); `Wrong letters` inline = upper-cased joined or `—`; `Players` (if any) = lines `<@uid> ❤️ n/LIVES` (cap 1024 chars); `Next level` inline = `discord.utils.format_dt(hangman.next_reset(), "R")`. While playing add `How to play` = "Type a single letter in this channel, reply to this post with the full level name, or use `/hangman guess`. Everyone gets {LIVES} ❤️ and wrong guesses only cost you." Color orange playing, green won, red lost. The title/footer/fields must never contain the name while playing (the board and redacted hint are the only name-derived content).
      - Helper `def hangman_note(user: discord.abc.User, r: hangman.GuessResult, g: hangman.Game) -> tuple[str, bool]` returning `(text, public)`: HIT `✅ {mention} found **{L}** ×{count}. ❤️ {lives}/{LIVES}` public; MISS letter `❌ No **{L}** in the name. {mention} has ❤️ {lives}/{LIVES} left.` public; MISS name `❌ {mention} guessed **{escaped}**, not it. ❤️ …` public; WIN `🎉 {mention} solved it! The level was **{name}** (#{pos}).` public; LOST_GAME `💀 Everyone who played is out of lives. The level was **{name}** (#{pos}).` public; REPEAT `**{L}** was already guessed, no life lost. You have ❤️ {lives}/{LIVES}.` private; OUT `You're out of lives for today's puzzle. The next one starts {format_dt(next_reset, "R")}.` private; FINISHED `Today's puzzle is finished. The next one starts …` private; INVALID `Guess a single letter/digit or the full level name.` private. User-supplied text goes through `discord.utils.escape_mentions(discord.utils.escape_markdown(...))`.
      - `async def send_hangman(channel, guild_id: int, note: str | None = None) -> discord.Message`: build embed for `hangman.game_for(...)`, send, then under `storage.lock` `g.remember_message(msg.id, channel.id)` + `storage.save()`.
      - `async def handle_hangman(message) -> bool` (called from `on_message` before `handle_answer`: `if await handle_hangman(message): return`): return False unless `hangman.current(storage.data["hangman"], hangman.utc_today())` (never hit the API from plain messages). Get the guild game state *without creating it* (`storage.data["hangman"]["guilds"].get(str(guild.id))`); return False if missing or finished. `replying = message.reference and message.reference.message_id in state["messages"]`; accept if `replying`, or `hangman.is_letter(content)` and `message.channel.id in state["channels"]`; else return False. Under `storage.lock`: `r = g.guess(author.id, message.content)`, `storage.save()`. Ignore INVALID/FINISHED silently (return True for INVALID on replies only). Private notes → `message.reply(text, mention_author=False)`; public → `message.reply(embed=hangman_embed(g, note), mention_author=False)` then remember the reply id. Wrap sends in `try/except discord.HTTPException` with `log.warning` (like `safe_react`).
      - Slash commands: `hangman_group = app_commands.Group(name="hangman", description="Daily AREDL hangman: guess today's Extreme Demon", guild_only=True)`; `client.tree.add_command(hangman_group)` (module level, before `setup_hook` syncs). Only `start` can hit the API (it defers first because the ~890 KB download can exceed Discord's 3 s limit). `guess`/`giveup` never fetch: if `hangman.current(storage.data["hangman"], hangman.utc_today())` is False they reply ephemerally "Today's puzzle isn't loaded yet. Run `/hangman start` first." This avoids mixing a public defer with ephemeral replies.
        - `start` ("Show today's AREDL hangman puzzle"): `await interaction.response.defer()`, `if not await hangman_ready()` → `followup.send("Couldn't reach the AREDL API right now. Try again in a bit.")` and return; else `msg = await interaction.followup.send(embed=hangman_embed(g, note), wait=True)`, then remember `msg.id`/channel under `storage.lock` + save. If finished, `note` says today's puzzle is finished and when the next one starts (no re-roll).
        - `guess` (`@app_commands.describe(guess="A single letter/digit, or the full level name")`): current-day check as above, `r = g.guess(...)` under `storage.lock` + save; private notes → `interaction.response.send_message(text, ephemeral=True)`; public → `interaction.response.send_message(embed=hangman_embed(g, note))`, then `msg = await interaction.original_response()` and remember its id/channel.
        - `giveup` ("Give up today's puzzle and see the answer (only you see it)"): current-day check; if finished, ephemeral finished note; else `g.give_up(user.id)` under lock + save, ephemeral `The level was **{name}** (#{pos}). You're out for today.`; if that made the game LOST, also `await interaction.channel.send(embed=hangman_embed(g, lost note))` and remember it.
      - Scheduler: `@tasks.loop(time=dt.time(hour=0, minute=0, second=30, tzinfo=dt.timezone.utc)) async def hangman_daily()` — if not `await hangman_ready()` return; note = `f"New day, new level! Yesterday's was **{prev['name']}** (#{prev['position']})."` when `previous` exists for the day before; for each `daily_channel_ids()` resolve channel like `daily_post` (get/fetch, skip on HTTPException), skip if no `guild`, `await send_hangman(channel, channel.guild.id, note)` inside `try/except discord.HTTPException: log.exception(...)`. `@hangman_daily.before_loop` reuses `client.wait_until_ready()` (add a second before_loop function). Start it in `setup_hook` after `daily_post.start()`. Comment: UTC from the system clock, 30 s offset so the date has already rolled.
      - Do not change `today()`, `TZ` usage, or the existing games.
      Files: `bot.py`
      Verify: `.venv\Scripts\python.exe -m py_compile bot.py` and `py -3.10 -m py_compile bot.py` succeed; `.venv\Scripts\python.exe -m unittest discover -s tests -v` still passes.

- [ ] 5. Add bot-level tests for the embed and command registration (depends on 4).
      Create `tests\test_bot_hangman.py`: before importing `bot`, set `os.environ["DISCORD_TOKEN"] = "test-token"` (load_dotenv won't override it, so the real token is never loaded into the test) and `os.environ["DATA_FILE"]` to a file in a `tempfile.mkdtemp()` dir so `data/state.json` isn't touched. Then: populate `bot.storage.data["hangman"]` via `hangman.ensure_day` with a fake level whose description contains its name (e.g. `NOMAD`, description "Nomad is a short precision test", tags `["Short"]`); assert an in-progress `bot.hangman_embed(g)` contains no case-insensitive occurrence of the name in title, description, any field name/value, or footer, and includes `Position`/`Hint`; with `description=None` the Hint field is `hangman.NO_DESCRIPTION` and a `Tags` field exists; after a winning guess the embed contains the name and is green; `bot.hangman_note` for REPEAT is private and for MISS includes `5/6`; `bot.client.tree.get_command("hangman")` is an `app_commands.Group` whose subcommand names are `{"start", "guess", "giveup"}`. Do not call `client.run` or any network.
      Files: `tests\test_bot_hangman.py`
      Verify: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — all tests pass, and `data\state.json` is unchanged (compare `Get-FileHash data\state.json` before and after).

- [ ] 6. Document the feature in README.
      Add a `🎯 Daily AREDL Hangman` bullet to the intro list, a `## Daily AREDL Hangman` section (one level per UTC day from the AREDL API, resets at 00:00 UTC via the system clock, per-guild board, 6 lives per player, letter messages / reply-with-name / `/hangman guess`, hint redaction and the "No description available." fallback, give-up is personal, finished stays finished until the next UTC day, auto-post to the daily channel at 00:00 UTC), the three commands in the Commands table, `python hangman.py` in Customizing, `hangman.py` and `tests/` in the Files list, and a "Run tests" line `python -m unittest discover -s tests -v`.
      Files: `README.md`
      Verify: `.venv\Scripts\python.exe -m unittest discover -s tests -v` passes; README renders the new table rows (visual check).

- [ ] 7. Final verification.
      Run `.venv\Scripts\python.exe -m unittest discover -s tests -v` (all pass), `py -3.10 -m py_compile bot.py hangman.py storage.py tests\test_hangman.py tests\test_bot_hangman.py` (no syntax errors), `.venv\Scripts\python.exe hangman.py` (live API: prints today's UTC date and masked board; run twice → same board). Confirm `requirements.txt` is unchanged and nothing was committed. Delete `__pycache__` folders created under `tests\` only if they weren't there before (they're gitignored, so leaving them is fine).

---

## Verification note (implementation, iteration 1)

Implemented: `storage.py` (`hangman` key), new `hangman.py`, `bot.py` integration (`/hangman start|guess|giveup` group, plain-message guesses in `on_message`, `hangman_daily` loop at 00:00:30 UTC), `README.md`.

Deviations from this plan, on purpose:
- Steps 3 and 5 (permanent `tests/`) were NOT done: the task brief says "Do NOT add permanent test files". The same coverage ran in a temporary script (below), which was then deleted. README has no "Run tests" line for the same reason.
- `redact()`: terms with 4+ letters/digits are also hidden inside other words; shorter terms only as whole words. Reason: live data showed word-boundary-only redaction leaked `#RateFragile` (Fragile) and `pistons` (PISTON); substring redaction for short names (`Io`, `Ion`) would mangle words like `radio`/`action`.
- "How to play" text says "Type a single letter or digit here" instead of "...in this channel": a real level is named `in this`, so the old wording leaked that name in every in-progress board.

Commands run (all from the project root) and results:
- `.venv\Scripts\python.exe -m py_compile hangman.py bot.py storage.py` -> exit 0 (Python 3.12.10).
- `py -3.10 -m py_compile hangman.py bot.py storage.py` -> exit 0 (stricter than prod 3.11 for syntax).
- Temporary `_tmp_verify_hangman.py` run with `.venv\Scripts\python.exe` (DISCORD_TOKEN set to a dummy and DATA_FILE to a temp dir before importing `bot`; `client.run` never called) -> `ALL OK: 6635 checks passed`, then deleted. It covered:
  - masking (`Artemis 13` -> `_ _ _ _ _ _ _  /  _ _`, with a,3 -> `A _ _ _ _ _ _  /  _ 3`, parens visible, reveal), normalize/is_letter, redaction;
  - letter guesses (case-insensitive, all occurrences, miss costs only the guesser, repeats free, 0 lives -> OUT, punctuation-only -> INVALID);
  - name guesses (`artemis-13` wins, wrong name costs a life, repeated wrong name free, letters completing the name -> WIN);
  - all players out -> LOST, finished blocks guesses, give-up personal / ends only when everyone is out;
  - null/""/blank description -> no hint; embed shows "No description available." + Tags + `#N • Legacy`;
  - AC1 determinism (order-independent, independent sha256 index, >=15 distinct over 30 days), AC2/AC3 `ensure_day` (same day keeps level/progress, rollover clears guilds + sets previous, empty list leaves data untouched), AC4 UTC clock incl. non-UTC aware input and `next_reset`;
  - `fetch_levels` with mocked `_download`: filters invalid items, cache hit within TTL, stale cache + 2 warnings on failure, `[]` with no cache;
  - `bot` import without Discord: `/hangman` is an `app_commands.Group` with {start, guess, giveup}; all existing commands still registered; in-progress embed has no name, board in a code block; MISS note shows `5/6`; REPEAT note private; `@everyone` escaped; win embed green with name;
  - LIVE API: 1643 levels parsed, 809 with null/blank description. For EVERY real level: board has no letters/digits, one `_` per alnum char, `/` count = word breaks, full-name guess (upper-cased, spaces->dashes) wins, and neither the in-progress embed nor the HIT/MISS/REPEAT/OUT/FINISHED/INVALID notes contain the name (0 leaks after the two fixes above). Samples: `Deimos (ItsHybrid)` -> `_ _ _ _ _ _  /  ( _ _ _ _ _ _ _ _ _ )`, `2 1 1` -> `_  /  _  /  _`, `IH8` -> `_ _ _`.
- `.venv\Scripts\python.exe hangman.py` twice -> same output both times (`2026-10-25 (UTC)`, board `_ _  /  _ _ _ _`, redacted hint).
- `data\state.json` SHA-256 unchanged before/after (6DB30D6F...9C23). `requirements.txt` unchanged. Nothing committed. (`.env.example` shows as deleted in `git status`; that was already the case before this work.)

Not verified: real Discord behavior (slash command sync, replies, ephemeral messages, the 00:00 UTC loop), since the bot was not connected.

## Verification note (iteration 2, review findings)

- Finding 1 (blocking, give-up ends the shared puzzle): fixed. `Game.give_up` now only sets the caller's lives to 0 and never sets `LOST`; `/hangman giveup` no longer posts the public "everyone is out" embed (the answer stays ephemeral). This supersedes the parenthetical in Decision 8 and matches the README.
  - `.venv\Scripts\python.exe -m py_compile hangman.py bot.py` -> exit 0; `py -3.10 -m py_compile hangman.py bot.py` -> exit 0.
  - Temporary `_tmp_verify_giveup.py` (dummy DISCORD_TOKEN, temp DATA_FILE, imports `bot`, no `client.run`) -> `giveup checks OK`, then deleted. Checked: first give-up leaves status `playing`, giver is `OUT` on later guesses, another user still hits with 6 lives; give-up when all others are at 0 keeps `playing` and a later full-name guess wins; give-up on a finished puzzle returns False; solo 6-miss loss rule unchanged; `hangman_giveup_cmd` has no `channel.send`.
- Finding 6: `.env.example` untouched; nothing committed.
- Findings 2, 3, 4, 5: change user-visible behavior, so they were raised with the user before editing. Answer: "2 change, 3 band, 4 ignore, 5 daily ok".
- Finding 2 (changed): running out of lives locks out only that player. `_miss` no longer sets `LOST`; `_everyone_out` and the `LOST_GAME` outcome/note were removed. The puzzle stays `playing` until someone solves it or the UTC day rolls over (`LIVES` still 6). The existing 00:00 UTC post already reveals yesterday's answer (`Yesterday's was **X** (#N)`). The `LOST` status constant and red color are kept so older saved state still loads. Supersedes Decision 6's loss rule.
- Finding 3 (band): new `hangman.position_band()`: `Top 100`, `#101–250`, `#251–500`, `#501–1000`, `#1001+`, or `Legacy`. The embed's `Position` field shows the band while playing and the exact `#N` (+ `• Legacy`) once finished. The win note, the private give-up answer and the next day's reveal still show `#N`. Supersedes Decision 11's "Position always shown".
- Finding 4 (ignore): in `handle_hangman` (plain messages and replies), private outcomes (REPEAT, OUT) are swallowed silently with no reply. Slash `/hangman guess` still gets the private note. Single letters and replies count as guesses as before.
- Finding 5: daily per-server model confirmed; no stop command.
- README updated: lives/lock-out, band wording, silent chat repeats, puzzle stays open until solved.
- Commands run (project root) and results:
  - `.venv\Scripts\python.exe -m py_compile hangman.py bot.py` -> exit 0; `py -3.10 -m py_compile hangman.py bot.py` -> exit 0.
  - Temporary `_tmp_verify_iter2.py` with `.venv\Scripts\python.exe` (dummy DISCORD_TOKEN, temp DATA_FILE, imports `bot`, no `client.run`, `message.reply` mocked) -> `ALL OK: 4977 checks passed`, then deleted. Covered: give-up personal (finding 1); 6 misses by one player -> all `MISS`, status stays `playing`, that player `OUT` for letters and names, others keep 6 lives and can still win, several players at 0 still `playing`, `LOST_GAME` gone; `position_band` limits 1/100/101/250/251/500/501/1000/1001/1609/Legacy/None; in-progress embed shows `Top 100` and no `#5` or name, solved embed shows `#5`, green, revealed board; `handle_hangman`: HIT replies with an embed, a repeated letter from another user / out-of-lives letter / out-of-lives reply / repeated wrong-name reply -> consumed with 0 replies, non-letter chat -> not consumed, finished -> not consumed; slash note for OUT still private. Live API: 1643 levels; bands Top 100=100, #101–250=150, #251–500=250, #501–1000=500, #1001+=609, Legacy=34; every board has no letters/digits and one `_` per alnum char; no in-progress embed contains the name as a word or the exact `#N` (0 leaks; 5 short names `Fog`, `Io`, `EX`, `UNG`, `Ion` only appear inside other words, e.g. "descript**ion**"). Samples: `Deimos (ItsHybrid)` -> `_ _ _ _ _ _  /  ( _ _ _ _ _ _ _ _ _ )`, `Artemis 13` -> `_ _ _ _ _ _ _  /  _ _`, `IH8` -> `_ _ _`.
  - `data\state.json` SHA-256 unchanged before/after. `requirements.txt` unchanged. Nothing committed.
- Not verified: real Discord behavior (slash sync, replies, ephemeral messages, the 00:00 UTC loop).
