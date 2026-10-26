# Daily AREDL hangman for the Discord bot (pass 2)

The bot gains a hangman game built on the AREDL level list. Each UTC day a level is picked deterministically (sha256 of the date over the list sorted by UUID), snapshotted into `state.json`, and played per guild. Players guess letters or the full name, with the redacted description (or tags when it's null/blank) as the hint, and every player has their own 6 lives. Since pass 1, give-up is personal only, running out of lives locks out just that player (the puzzle stays open until solved or the UTC day rolls over), the exact position became a band while playing, and chat-path repeats and out-of-lives guesses are swallowed silently. All six pass-1 findings are fixed or were settled with the user ("2 change, 3 band, 4 ignore, 5 daily ok").

Watch for: an unsolved puzzle's answer only becomes public through the 00:00 UTC auto-post, so a guild with no daily channel that never solves the level never sees the answer (confirmed, non-blocking).

**Verdict**: APPROVED

## High-level view

The game is a daily puzzle shared by a guild, which the user confirmed over one game per channel with a stop command. With `LOST` no longer reachable, the only end states are a win or the day rolling over, so lives are independent per player. The out-of-lives check runs before the name comparison, so a player can't give up, read the answer privately, and then claim the win.

In-progress embeds expose only the code-block board, the redacted hint (or "No description available." plus tags) and a position band. The coder's live sweep over all 1643 levels found 0 name leaks.

The gap is the reveal path. Yesterday's answer is announced only by `hangman_daily`, which posts only to configured daily channels. A guild that plays through `/hangman start` without a daily channel, and doesn't solve the level, never gets a public answer.

<details>
<summary>Issues (1)</summary>

1. **No answer reveal without a daily channel** (non-blocking, confirmed): `hangman_daily` is the only place yesterday's name is shown publicly, and it only posts to `daily_channel_ids()`. Guilds without a daily channel never see the answer of an unsolved puzzle, despite the README's "the next day's post reveals yesterday's level either way". Either show `previous` in the first `/hangman start` board of the new day, or narrow the README wording to the daily channel.

</details>

<details>
<summary>Details</summary>

### Reveal path for unsolved puzzles

With no loss state, an unsolved puzzle ends silently at 00:00 UTC. `ensure_day` stores the old name in `previous`, and the only reader is `hangman_daily`:

```python
for channel_id in daily_channel_ids():
    ...
    await send_hangman(channel, guild.id, note)  # note includes "Yesterday's was **X** (#N)"
```

`hangman_start_cmd` builds its board with `note=None` (or the "finished" note), so in a guild that never ran `/setchannel` (and isn't `DAILY_CHANNEL_ID`) the answer is never posted. The only way to learn it is `/hangman giveup`, which is private. The README promises "the next day's post reveals yesterday's level either way", which only holds for daily channels. Confidence: confirmed.

### Verification evidence

The plan records `py_compile` on `hangman.py`, `bot.py` and `storage.py` under 3.12 and 3.10 (exit 0) for both iterations, plus temporary scripts that were run and then deleted: `ALL OK: 6635 checks passed` (iteration 1), and `ALL OK: 4977 checks passed` and `giveup checks OK` (iteration 2). The iteration-2 script covers the reworked give-up and lock-out, `position_band` limits, silent chat repeats/out, and a live leak sweep. `requirements.txt` is unchanged. Not tested: real Discord behavior (slash sync, replies, ephemeral messages, the 00:00:30 UTC loop).

</details>

<details>
<summary>File map</summary>

- `hangman.py` (new): AREDL client with 6 h in-memory cache and stale fallback, UTC clock helpers, deterministic daily pick, `ensure_day` rollover, mask/normalize/redact, `position_band`, `Game` with per-user lives.
- `bot.py`: `hangman_ready`, `hangman_embed`, `hangman_note`, `handle_hangman` in `on_message`, `/hangman start|guess|giveup` group, `hangman_daily` loop at 00:00:30 UTC.
- `storage.py`: `hangman` key default.
- `README.md`: feature section, commands, files list.
- `.env.example`: shows as deleted, unrelated to this change (flagged in pass 1).

Full diff: `git diff` in `epic-bot-discord` plus untracked `hangman.py`.

</details>
