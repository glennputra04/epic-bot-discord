# Per-user rate limiting and per-feature daily channels

The bot gets two features. The first is an in-memory token-bucket limiter (`ratelimit.py`) applied centrally to every slash command through a `CommandTree.interaction_check` override, and at the decision point of each chat guess handler. The second replaces the single shared `/setchannel` setting with one channel per guild per feature (`qotd`, `crossword`, `wordsearch`, `aredl`), managed through `/set-daily-<feature>`, `/unset-daily` and `/daily-channels`. Old `settings[gid].channel_id` values are migrated once on load into every feature, and marked "legacy" so the `DAILY_CHANNEL_ID` env default keeps posting next to them exactly as before. The only deviations from the plan are the ones the coder recorded. Verification evidence is present: py_compile on 3.12 and 3.10, plus a 140-check temp script covering the limiter, storage, migration, loops, tree and message paths.

Watch for: the user's guess token is spent before the channel cap is checked, so a busy channel quietly drains a player's own budget (confirmed, minor). The silent channel cap of 5 guesses per 5 s can swallow legitimate hangman letters in an active group game with no feedback (confirmed, UX, matches plan decision 12).

**Verdict**: APPROVED

## High-level view

The command limiter sits in one place: `RateLimitedTree.interaction_check`. Every slash command goes through it, including the `/hangman` group subcommands and all the setup commands, and autocomplete interactions are passed through. When a user is over the limit, the first blocked call gets one ephemeral "⏳ Slow down" notice. Later blocked calls get no response until the user has an allowed hit again. Nothing in the bot uses component interactions (the only buttons are link buttons), so no interaction path bypasses the tree.

On the message path, `allow_guess` is called only after each handler has decided the message is a real guess, and before any state change, save, reaction or reply. That covers both `handle_hangman` (single letter in a board channel, or a reply to a board) and `handle_answer` (parsed answers that match a puzzle number). Blocked guesses are dropped silently, and a blocked hangman guess returns True so it can't fall through to answer checking. Ordinary chat never costs tokens. Buckets idle for `per` seconds get swept at most once a minute, and dropping them is lossless, so memory stays bounded by recently active keys.

Each loop posts only its own features. `daily_post` builds a channel→features plan from `qotd`/`crossword`/`wordsearch`, and `hangman_daily` iterates `daily_channel_ids("aredl")` only. The `DAILY_CHANNEL_ID` fallback is dropped for a feature only when the env channel's own guild has explicitly set or unset that feature with a new command. Migrated legacy entries don't count as explicit, so no existing guild or env deployment loses a post at deploy time.

All five setup commands are `guild_only` with `default_permissions(manage_guild=True)`, the same as the old `/setchannel`. They reply ephemerally. Before storing anything, they reject non-text targets and any channel where the bot lacks View Channel, Send Messages (or Send Messages in Threads) or Embed Links. The README now says the hangman reveal appears in the AREDL daily channel, which matches `hangman_daily`.

<details>
<summary>Issues (2)</summary>

1. **User token spent on channel-capped guesses**: `allow_guess` charges the user bucket before checking the channel bucket, so when the channel cap blocks a guess the player still loses a token. Check the channel bucket first, or refund/peek the user bucket, if this matters in practice. Non-blocking.
2. **Silent channel cap in group hangman**: with 5 acted-on guesses per 5 s per channel, letters from an active group get dropped with no sign. Consider a one-time ⏳ reaction per window, or a looser default for `RATELIMIT_CHANNEL`. Non-blocking, deliberate per plan.

</details>

<details>
<summary>Details</summary>

### Guess-path ordering in `allow_guess`

```python
def allow_guess(message):
    if not guess_limiter.hit(message.author.id).allowed:
        return False
    return channel_limiter.hit(message.channel.id).allowed
```

When the user bucket allows a guess and the channel bucket then blocks it, the user's token is already gone (confirmed). In a busy channel, a player whose guesses keep losing to the channel cap can burn through their own 6/10 s budget without the bot acting on any of it. A user blocked first doesn't consume a channel token, so the asymmetry only goes one way.

The channel cap is silent by design (plan decision 12), and it's matched to Discord's own 5 messages / 5 s, so the bot can't queue 429s. The flip side is hangman: every acted-on guess produces a public board reply, so a group of five or six people typing letters will regularly exceed one guess per second, and the extra letters just vanish (confirmed behavior, UX impact likely). This is non-blocking because the plan chose it deliberately and the env var lets operators loosen it, but it's the first knob to look at if players complain.

### Migration and the `DAILY_CHANNEL_ID` fallback

```
settings[gid].channel_id = X   ──migrate──►  daily_channels[gid] = {all 4: X}
                                             daily_legacy[gid]   = [all 4]

daily_targets(f) = { every guild's daily_channels[*][f] (non-None) }
                 ∪ { DAILY_CHANNEL_ID  unless is_explicit(env_guild, f) }
                       is_explicit = entry present AND not in daily_legacy
```

The migration pops `channel_id`, so it can't re-run and override a later `/unset-daily`. An uncached env channel (`default_guild_id()` is None) keeps the env default for every feature. Once deployed, the migrated file can't be read by the old code (`settings[gid]` is deleted), so a rollback needs `/setchannel` to be re-run. The plan acknowledges this.

</details>

<details>
<summary>File map</summary>

- `ratelimit.py` (new): token-bucket `RateLimiter` with `Verdict.notify`, sweep-based eviction, injectable `clock`, `parse_rate` and default constants.
- `storage.py`: `DAILY_FEATURES`, `daily_channels`/`daily_legacy` schema, one-time legacy migration, `set_daily_channel`, `daily_targets` with env fallback.
- `bot.py`: `RateLimitedTree`, `allow_guess` hooks in `handle_hangman`/`handle_answer`, per-feature `daily_channel_ids`/`daily_post_plan`/`resolve_channel`, rewritten `daily_post`/`hangman_daily`/`answer_channel_ids`, the `/set-daily-*`, `/unset-daily`, `/daily-channels` commands, and `/setchannel` reworked as a set-all shortcut.
- `README.md`: new commands, "Daily channels" and "Rate limits" sections, AREDL-channel reveal wording.

Full diff: `git diff HEAD` in `epic-bot-discord` (bot.py's diff also contains the earlier, uncommitted hangman work).

</details>
