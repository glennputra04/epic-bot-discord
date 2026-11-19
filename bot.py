"""Daily Discord bot: Question of the Day + Daily Crossword + Daily Word Search + AREDL Hangman + AREDL RNG."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import os
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()  # before importing puzzel, which reads its settings from the environment

import answers  # noqa: E402
import aredl_rng  # noqa: E402
import game  # noqa: E402
import hangman  # noqa: E402
import puzzel  # noqa: E402
import ratelimit  # noqa: E402
from qotd import fetch_question  # noqa: E402
from storage import DAILY_FEATURES, Storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dailybot")

TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.getenv("GUILD_ID") or 0) or None
DEFAULT_CHANNEL_ID = int(os.getenv("DAILY_CHANNEL_ID") or 0) or None
TZ = ZoneInfo(os.getenv("TIMEZONE", "UTC"))
POST_HOUR, POST_MINUTE = (int(x) for x in os.getenv("POST_TIME", "09:00").split(":"))
QOTD_CATEGORY = os.getenv("QOTD_CATEGORY", "general")
DATA_FILE = os.getenv("DATA_FILE", "data/state.json")
LEADERBOARD_FILE = os.getenv("LEADERBOARD_FILE") or None  # default: leaderboard.json next to DATA_FILE
INVENTORY_FILE = os.getenv("INVENTORY_FILE") or None  # default: inventory.json next to DATA_FILE

# Rate limits as "count/seconds" (see ratelimit.py for the defaults).
command_limiter = ratelimit.RateLimiter(*ratelimit.parse_rate(os.getenv("RATELIMIT_COMMANDS"), ratelimit.COMMANDS))
guess_limiter = ratelimit.RateLimiter(*ratelimit.parse_rate(os.getenv("RATELIMIT_GUESSES"), ratelimit.GUESSES))
channel_limiter = ratelimit.RateLimiter(*ratelimit.parse_rate(os.getenv("RATELIMIT_CHANNEL"), ratelimit.CHANNEL))

CORRECT, WRONG = "✅", "❌"
storage = Storage(DATA_FILE, LEADERBOARD_FILE, INVENTORY_FILE)
puzzel_lock = asyncio.Lock()
hangman_lock = asyncio.Lock()
puzzel_attempted: set[tuple[str, str]] = set()  # (day, kind) already tried since startup


def today() -> str:
    return dt.datetime.now(TZ).date().isoformat()


def yesterday() -> str:
    return (dt.datetime.now(TZ).date() - dt.timedelta(days=1)).isoformat()


# --------------------------------------------------------------------------- #
# Daily content
# --------------------------------------------------------------------------- #
async def question_for(day: str) -> dict | None:
    """Fetch the day's question from boneitis.org once, then serve it from storage."""
    async with storage.lock:
        q = storage.data["questions"].get(day)
    if q is None:
        q = await fetch_question(QOTD_CATEGORY)
        if q is not None:
            async with storage.lock:
                storage.data["questions"][day] = q
                storage.save()
    return q


async def puzzel_links(day: str) -> dict[str, str]:
    """Create today's Puzzel activities once per day (the API allows 10 per day)."""
    async with puzzel_lock:
        links = storage.data["links"].setdefault(day, {})
        if not puzzel.enabled():
            return links
        cw, ws = game.puzzles_for(day)
        jobs = {
            game.CROSSWORD: lambda: puzzel.create_crossword(
                f"Daily Crossword {day}", [(e.answer, e.clue) for e in cw.entries]
            ),
            game.WORDSEARCH: lambda: puzzel.create_wordsearch(
                f"Daily Word Search {day}", [w for w, _ in ws.words]
            ),
        }
        for kind, create in jobs.items():
            if kind in links or (day, kind) in puzzel_attempted:
                continue
            puzzel_attempted.add((day, kind))
            url = await create()
            if url:
                links[kind] = url
        async with storage.lock:
            storage.save()
        return links


async def hangman_ready() -> bool:
    """Make sure this round's (12 h, UTC) hangman level is chosen. Only downloads when the round changes."""
    day = hangman.current_round()
    if hangman.current(storage.data["hangman"], day):
        return True
    async with hangman_lock:
        if not hangman.current(storage.data["hangman"], day):
            levels = await hangman.fetch_levels()
            async with storage.lock:
                if hangman.ensure_day(storage.data["hangman"], day, levels):
                    storage.save()
    ready = hangman.current(storage.data["hangman"], day)
    if not ready:
        log.warning("No AREDL levels available for hangman %s", day)
    return ready


def link_view(url: str | None, label: str) -> Any:
    """A link button for the Puzzel activity, or MISSING (no view) when there's no link."""
    if not url:
        return discord.utils.MISSING
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=label, url=url, emoji="🔗"))
    return view


# --------------------------------------------------------------------------- #
# Embeds
# --------------------------------------------------------------------------- #
def qotd_embed(q: dict | None, day: str) -> discord.Embed:
    if q is None:
        return discord.Embed(
            title=f"💬 Question of the Day — {day}",
            description="Couldn't reach the question service today. Try `/qotd` again later!",
            color=discord.Color.light_grey(),
        )
    embed = discord.Embed(
        title=f"💬 Question of the Day — {q.get('date', day)}",
        description=f"**{q['question']}**",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Category: {q.get('category', QOTD_CATEGORY)} • via boneitis.org")
    return embed


def crossword_embed(day: str, solved: set[str] | None = None, reveal: bool = False, link: str | None = None):
    cw, _ = game.puzzles_for(day)
    embed = discord.Embed(
        title=f"🧩 Daily Crossword — {day}" + (" (solution)" if reveal else ""),
        description=f"```\n{cw.render(solved, reveal)}\n```\n{cw.clues_text()}",
        color=discord.Color.green() if reveal else discord.Color.orange(),
    )
    if not reveal:
        how = "Reply here with `1 across apple`, `1d apple` or `1. apple`. Wrap answers in \\|\\|spoilers\\|\\| to hide them."
        if link:
            how += f"\nOr play it on [Puzzel.org]({link}) (clue numbers there may differ)."
        embed.add_field(name="How to answer", value=how, inline=False)
    return embed


def wordsearch_embed(day: str, found: set[int] | None = None, reveal: bool = False, link: str | None = None):
    _, ws = game.puzzles_for(day)
    grid = ws.render(found, reveal=reveal)
    if reveal:
        clues = "\n".join(f"`{i:>2}.` {w} — {c}" for i, (w, c) in enumerate(ws.words, 1))
    else:
        clues = "\n".join(
            f"`{i:>2}.` " + (f"~~{c}~~ **{w}**" if found and i in found else f"{c} ({len(w)})")
            for i, (w, c) in enumerate(ws.words, 1)
        )
    embed = discord.Embed(
        title=f"🔎 Daily Word Search — {day}" + (" (solution)" if reveal else ""),
        description=f"```\n{grid}\n```\n{clues}",
        color=discord.Color.green() if reveal else discord.Color.teal(),
    )
    if not reveal:
        how = ("Work out each word from its clue, find it in the grid (any direction), "
               "then reply here with `3. apple`.")
        if link:
            how += f"\nOr play it on [Puzzel.org]({link})."
        embed.add_field(name="How to answer", value=how, inline=False)
    return embed


def hangman_embed(g: hangman.Game, note: str | None = None) -> discord.Embed:
    # While playing, the masked board and the redacted hint are the only name-derived content.
    colors = {hangman.PLAYING: discord.Color.orange(), hangman.WON: discord.Color.green(),
              hangman.LOST: discord.Color.red()}
    board = f"```\n{g.board()}\n```"
    embed = discord.Embed(
        title=f"🎯 AREDL Hangman — {hangman.round_label(storage.data['hangman']['day'])} (UTC)",
        description=f"{note}\n\n{board}" if note else board,
        color=colors.get(g.state["status"], discord.Color.orange()),
    )
    hint = g.hint()
    embed.add_field(name="Hint", value=(hint or hangman.NO_DESCRIPTION)[:1024], inline=False)
    if hint is None and g.tags:
        embed.add_field(name="Tags", value=", ".join(g.tags)[:1024], inline=False)
    if g.finished:
        position = f"#{g.position}" + (" • Legacy" if g.level.get("status") == "Legacy" else "")
    else:
        # The exact #N is a direct lookup key for the answer, so only show a band while playing.
        position = hangman.position_band(g.position, g.level.get("status"))
    embed.add_field(name="Position", value=position, inline=True)
    embed.add_field(name="Wrong letters", value=" ".join(ch.upper() for ch in g.state["wrong"]) or "—", inline=True)
    embed.add_field(name="Next level", value=discord.utils.format_dt(hangman.next_reset(), "R"), inline=True)
    players = ""
    for uid, n in g.players().items():
        line = f"<@{uid}> ❤️ {n}/{hangman.LIVES}\n"
        if len(players) + len(line) > 1024:
            break
        players += line
    if players:
        embed.add_field(name="Players", value=players, inline=False)
    if not g.finished:
        embed.add_field(
            name="How to play",
            # Keep this wording free of level names (e.g. a level is called "in this").
            value=("Type a single letter or digit here, reply to this post with the full level name, "
                   f"or use `/hangman guess`. Everyone gets {hangman.LIVES} ❤️ and wrong guesses only cost you."),
            inline=False,
        )
    embed.set_footer(text="Levels via aredl.net • new level every 12 hours (00:00 and 12:00 UTC)")
    return embed


def hangman_note(user: discord.abc.User, r: hangman.GuessResult, g: hangman.Game) -> tuple[str, bool]:
    """The reply text for a guess and whether it's public (posted with the board)."""
    lives = f"❤️ {r.lives}/{hangman.LIVES}"
    answer = f"**{discord.utils.escape_markdown(g.name)}** (#{g.position})"
    later = f"The next one starts {discord.utils.format_dt(hangman.next_reset(), 'R')}."
    guess = discord.utils.escape_mentions(discord.utils.escape_markdown(r.value))
    if r.outcome == hangman.HIT:
        return f"✅ {user.mention} found **{guess}** ×{r.count}. {lives}", True
    if r.outcome == hangman.MISS and r.kind == "letter":
        return f"❌ No **{guess}** in the name. {user.mention} has {lives} left.", True
    if r.outcome == hangman.MISS:
        return f"❌ {user.mention} guessed **{guess}**, not it. {lives} left.", True
    if r.outcome == hangman.WIN:
        return f"🎉 {user.mention} solved it! The level was {answer}.", True
    if r.outcome == hangman.REPEAT:
        return f"**{guess}** was already guessed, no life lost. You have {lives}.", False
    if r.outcome == hangman.OUT:
        return f"You're out of lives for this puzzle. {later}", False
    if r.outcome == hangman.FINISHED:
        return f"This puzzle is finished. {later}", False
    return "Guess a single letter/digit or the full level name.", False


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class RateLimitedTree(app_commands.CommandTree):
    """Applies the per-user command limit to every slash command in one place."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.type is not discord.InteractionType.application_command:
            return True  # autocomplete etc.
        verdict = command_limiter.hit(interaction.user.id)
        if verdict.allowed:
            return True
        if verdict.notify:  # only once per cooldown; later blocked hits get no response
            seconds = max(1, math.ceil(verdict.retry_after))
            try:
                await interaction.response.send_message(f"⏳ Slow down, try again in {seconds}s.", ephemeral=True)
            except discord.HTTPException:
                pass
        return False


def allow_guess(message: discord.Message) -> bool:
    """Per-user, then per-channel limit for chat guesses. Blocked guesses are dropped silently."""
    if not guess_limiter.hit(message.author.id).allowed:
        return False
    return channel_limiter.hit(message.channel.id).allowed


class DailyBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # needed to read "1. apple" answers
        super().__init__(intents=intents)
        self.tree = RateLimitedTree(self)

    async def setup_hook(self) -> None:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        daily_post.start()
        hangman_daily.start()
        rng_hourly.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s; daily post at %02d:%02d %s; Puzzel %s",
                 self.user, POST_HOUR, POST_MINUTE, TZ.key, "on" if puzzel.enabled() else "off")
        log.info("Rate limits: commands %d/%gs per user, guesses %d/%gs per user, %d/%gs per channel",
                 command_limiter.rate, command_limiter.per, guess_limiter.rate, guess_limiter.per,
                 channel_limiter.rate, channel_limiter.per)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if await handle_collect(message):
            return
        if await handle_hangman(message):
            return
        await handle_answer(message)


client = DailyBot()


async def remember_post(day: str, message: discord.Message, kind: str) -> None:
    async with storage.lock:
        storage.data["puzzle_posts"].setdefault(day, {})[str(message.id)] = kind
        channels = storage.data["puzzle_channels"].setdefault(day, [])
        if message.channel.id not in channels:
            channels.append(message.channel.id)
        storage.save()


def answer_channel_ids(day: str) -> set[int]:
    ids = set(storage.data["puzzle_channels"].get(day, []))
    ids.update(daily_channel_ids(game.CROSSWORD), daily_channel_ids(game.WORDSEARCH))
    if DEFAULT_CHANNEL_ID:
        ids.add(DEFAULT_CHANNEL_ID)
    return ids


async def handle_answer(message: discord.Message) -> None:
    day = today()
    channel_ids = {message.channel.id, getattr(message.channel, "parent_id", None)}
    if not channel_ids & answer_channel_ids(day):
        return

    parsed = answers.parse(message.content)
    if parsed is None:
        return
    number, direction, guess = parsed

    # Replying to the crossword or word search post limits the check to that puzzle.
    only = None
    if message.reference and message.reference.message_id:
        only = storage.data["puzzle_posts"].get(day, {}).get(str(message.reference.message_id))

    result = game.check(day, number, direction, guess, only)
    if result is None:
        return  # e.g. "10 minutes" — no puzzle has that number, so it's just chat
    if not allow_guess(message):
        return
    if result is False:
        await safe_react(message, WRONG)
        return

    kind, key = result
    cw, ws = game.puzzles_for(day)
    total = len(cw.entries) if kind == game.CROSSWORD else len(ws.words)
    completed = False
    async with storage.lock:
        solved = storage.solves(day, message.author.id)[kind]
        if key not in solved:
            solved.append(key)
            score = storage.score(message.author.id)
            score["words"] += 1
            if len(solved) == total:
                completed = True
                score["crosswords" if kind == game.CROSSWORD else "wordsearches"] += 1
            storage.save()

    await safe_react(message, CORRECT)
    if completed:
        name = "crossword" if kind == game.CROSSWORD else "word search"
        await message.channel.send(f"🎉 {message.author.mention} completed today's {name}!")


async def safe_react(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException as exc:
        log.warning("Couldn't react in #%s: %s (needs Add Reactions + Read Message History)", message.channel, exc)


async def remember_hangman(g: hangman.Game, message: discord.Message) -> None:
    async with storage.lock:
        g.remember_message(message.id, message.channel.id)
        storage.save()


async def send_hangman(channel: discord.abc.Messageable, guild_id: int, note: str | None = None) -> discord.Message:
    g = hangman.game_for(storage.data["hangman"], guild_id)
    msg = await channel.send(embed=hangman_embed(g, note))
    await remember_hangman(g, msg)
    return msg


async def hangman_video_text(g: hangman.Game) -> str | None:
    """The 'here's the level' follow-up for a solved board, or None if AREDL has no video.
    A found URL is saved with today's level so other servers' wins don't ask the API again."""
    url = g.level.get("video")
    if not url:
        url = await hangman.fetch_video(g.level["id"])
        if not url:
            return None
        async with storage.lock:
            g.level["video"] = url
            storage.save()
    # Sent as plain text so Discord shows its video player (embeds can't play videos).
    return f"🎬 **{discord.utils.escape_markdown(g.name)}** verification: {url}"


async def handle_hangman(message: discord.Message) -> bool:
    """Treat a single letter in a channel with today's board, or a reply to the board,
    as a hangman guess. Returns True when the message was a guess."""
    data = storage.data["hangman"]
    if not hangman.current(data, hangman.current_round()):
        return False  # plain messages never hit the API; /hangman start loads the round
    state = data["guilds"].get(str(message.guild.id))  # type: ignore[union-attr]
    if state is None or state.get("status", hangman.PLAYING) != hangman.PLAYING:
        return False
    replying = bool(message.reference and message.reference.message_id in state.get("messages", []))
    if not replying and not (hangman.is_letter(message.content) and message.channel.id in state.get("channels", [])):
        return False
    if not allow_guess(message):
        return True  # rate limited: drop silently, and don't treat it as a crossword answer

    g = hangman.Game(data["level"], state)
    async with storage.lock:
        r = g.guess(message.author.id, message.content)
        storage.save()
    if r.outcome in (hangman.INVALID, hangman.FINISHED):
        return replying
    text, public = hangman_note(message.author, r, g)
    if not public:
        return True  # repeats / out of lives: stay quiet in chat (slash guesses still get a private note)
    try:
        reply = await message.reply(embed=hangman_embed(g, text), mention_author=False)
        await remember_hangman(g, reply)
        if r.outcome == hangman.WIN and (video := await hangman_video_text(g)):
            await message.channel.send(video)
    except discord.HTTPException as exc:
        log.warning("Couldn't reply to a hangman guess in #%s: %s", message.channel, exc)
    return True


async def post_daily(channel: discord.abc.Messageable) -> None:
    day = today()
    q = await question_for(day)
    await channel.send(embed=qotd_embed(q, day))
    await send_crossword(channel, day)
    await send_wordsearch(channel, day)


async def send_crossword(channel: discord.abc.Messageable, day: str) -> discord.Message:
    link = (await puzzel_links(day)).get(game.CROSSWORD)
    msg = await channel.send(embed=crossword_embed(day, link=link), view=link_view(link, "Play on Puzzel"))
    await remember_post(day, msg, game.CROSSWORD)
    return msg


async def send_wordsearch(channel: discord.abc.Messageable, day: str) -> discord.Message:
    link = (await puzzel_links(day)).get(game.WORDSEARCH)
    msg = await channel.send(embed=wordsearch_embed(day, link=link), view=link_view(link, "Play on Puzzel"))
    await remember_post(day, msg, game.WORDSEARCH)
    return msg


POST_FEATURES = ("qotd", game.CROSSWORD, game.WORDSEARCH)  # posted by daily_post, in this order


def default_guild_id() -> int | None:
    """The guild of DAILY_CHANNEL_ID, or None if it's unset or not cached."""
    if not DEFAULT_CHANNEL_ID:
        return None
    guild = getattr(client.get_channel(DEFAULT_CHANNEL_ID), "guild", None)
    return getattr(guild, "id", None)


def daily_channel_ids(feature: str) -> set[int]:
    """Channels that get ``feature``'s daily post (configured ones + the DAILY_CHANNEL_ID default)."""
    return storage.daily_targets(feature, DEFAULT_CHANNEL_ID, default_guild_id())


def daily_post_plan() -> dict[int, list[str]]:
    """channel id -> the POST_FEATURES to post there, in POST_FEATURES order."""
    plan: dict[int, list[str]] = {}
    for feature in POST_FEATURES:
        for channel_id in daily_channel_ids(feature):
            plan.setdefault(channel_id, []).append(feature)
    return plan


async def resolve_channel(channel_id: int) -> discord.abc.Messageable | None:
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except discord.HTTPException:
            log.warning("Can't access channel %s", channel_id)
            return None
    return channel  # type: ignore[return-value]


@tasks.loop(time=dt.time(hour=POST_HOUR, minute=POST_MINUTE, tzinfo=TZ))
async def daily_post() -> None:
    async with storage.lock:
        storage.prune({today(), yesterday()})
        storage.save()
    day = today()
    question: list[dict | None] = []  # fetched lazily, once per run
    for channel_id, features in daily_post_plan().items():
        channel = await resolve_channel(channel_id)
        if channel is None:
            continue
        try:
            for feature in features:
                if feature == "qotd":
                    if not question:
                        question.append(await question_for(day))
                    await channel.send(embed=qotd_embed(question[0], day))
                elif feature == game.CROSSWORD:
                    await send_crossword(channel, day)
                elif feature == game.WORDSEARCH:
                    await send_wordsearch(channel, day)
        except discord.HTTPException:
            log.exception("Failed to post daily content to %s", channel_id)


@daily_post.before_loop
async def _wait_ready() -> None:
    await client.wait_until_ready()


# Hangman rounds are 12 h in UTC from the system clock (not TIMEZONE). Posting 30 s after
# each boundary makes sure the round has already rolled over when the loop wakes up.
@tasks.loop(time=[dt.time(hour=h, minute=0, second=30, tzinfo=dt.timezone.utc)
                  for h in range(0, 24, hangman.ROUND_HOURS)])
async def hangman_daily() -> None:
    if not await hangman_ready():
        return
    data = storage.data["hangman"]
    prev = data.get("previous")
    note = "New round, new level!"
    if prev and prev.get("day") == hangman.previous_round():
        note += f" The last one was **{discord.utils.escape_markdown(prev['name'])}** (#{prev['position']})."
    for channel_id in daily_channel_ids("aredl"):
        channel = await resolve_channel(channel_id)
        if channel is None:
            continue
        guild = getattr(channel, "guild", None)
        if guild is None:
            continue
        try:
            await send_hangman(channel, guild.id, note)  # type: ignore[arg-type]
        except discord.HTTPException:
            log.exception("Failed to post hangman to %s", channel_id)


@hangman_daily.before_loop
async def _wait_ready_hangman() -> None:
    await client.wait_until_ready()


# --------------------------------------------------------------------------- #
# Slash commands
# --------------------------------------------------------------------------- #
@client.tree.command(name="qotd", description="Show today's Question of the Day")
async def qotd_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    day = today()
    await interaction.followup.send(embed=qotd_embed(await question_for(day), day))


@client.tree.command(name="crossword", description="Show today's crossword puzzle")
async def crossword_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    day = today()
    link = (await puzzel_links(day)).get(game.CROSSWORD)
    msg = await interaction.followup.send(
        embed=crossword_embed(day, link=link), view=link_view(link, "Play on Puzzel"), wait=True
    )
    await remember_post(day, msg, game.CROSSWORD)


@client.tree.command(name="wordsearch", description="Show today's word search")
async def wordsearch_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    day = today()
    link = (await puzzel_links(day)).get(game.WORDSEARCH)
    msg = await interaction.followup.send(
        embed=wordsearch_embed(day, link=link), view=link_view(link, "Play on Puzzel"), wait=True
    )
    await remember_post(day, msg, game.WORDSEARCH)


@client.tree.command(name="progress", description="Your progress on today's puzzles (only visible to you)")
async def progress_cmd(interaction: discord.Interaction) -> None:
    day = today()
    cw, ws = game.puzzles_for(day)
    solves = storage.data["solves"].get(day, {}).get(str(interaction.user.id), {})
    cw_solved = set(solves.get(game.CROSSWORD, []))
    ws_found = {int(k) for k in solves.get(game.WORDSEARCH, [])}

    cw_embed = crossword_embed(day, cw_solved)
    cw_embed.clear_fields().add_field(name="Solved", value=f"{len(cw_solved)}/{len(cw.entries)}")
    ws_embed = wordsearch_embed(day, ws_found)
    ws_embed.clear_fields().add_field(name="Found", value=f"{len(ws_found)}/{len(ws.words)}")
    await interaction.response.send_message(embeds=[cw_embed, ws_embed], ephemeral=True)


@client.tree.command(name="yesterday", description="Reveal yesterday's crossword and word search answers")
async def yesterday_cmd(interaction: discord.Interaction) -> None:
    day = yesterday()
    await interaction.response.send_message(
        embeds=[crossword_embed(day, reveal=True), wordsearch_embed(day, reveal=True)]
    )


@client.tree.command(name="leaderboard", description="Top puzzle solvers")
async def leaderboard_cmd(interaction: discord.Interaction) -> None:
    scores = {uid: storage.score(int(uid)) for uid in storage.data["scores"]}
    ranked = sorted(scores.items(), key=lambda kv: kv[1]["words"], reverse=True)[:10]
    if not ranked:
        await interaction.response.send_message("No scores yet. Solve a crossword or word search clue!")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"{medals[i] if i < 3 else f'`{i + 1}.`'} <@{uid}> — **{s['words']}** words "
        f"({s['crosswords']} crosswords, {s['wordsearches']} word searches completed)"
        for i, (uid, s) in enumerate(ranked)
    ]
    embed = discord.Embed(title="🏆 Leaderboard", description="\n".join(lines), color=discord.Color.gold())
    embed.set_footer(text="1 point per solved crossword clue or found word")
    await interaction.response.send_message(embed=embed)


# --------------------------------------------------------------------------- #
# Daily channel setup (one channel per feature per server)
# --------------------------------------------------------------------------- #
DAILY_LABELS = {
    "qotd": "💬 Question of the Day",
    game.CROSSWORD: "🧩 Daily Crossword",
    game.WORDSEARCH: "🔎 Daily Word Search",
    "aredl": "🎯 Daily AREDL Hangman",
}


def post_time(feature: str) -> str:
    if feature in POST_FEATURES:
        return f"{POST_HOUR:02d}:{POST_MINUTE:02d} {TZ.key}"
    return "00:00 and 12:00 UTC"


def missing_perms(channel: discord.TextChannel | discord.Thread, me: discord.Member,
                  needed: tuple[str, ...]) -> list[str]:
    perms = channel.permissions_for(me)
    return [name.replace("_", " ").title() for name in needed if not getattr(perms, name)]


async def set_daily(interaction: discord.Interaction, features: tuple[str, ...],
                    channel: discord.TextChannel | None) -> None:
    """Point ``features`` at ``channel`` (default: this channel) after checking the bot can post there."""
    target = channel or interaction.channel
    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("Daily posts need a text channel or a thread.", ephemeral=True)
        return
    me = interaction.guild.me  # type: ignore[union-attr]
    send = "send_messages_in_threads" if isinstance(target, discord.Thread) else "send_messages"
    missing = missing_perms(target, me, ("view_channel", send, "embed_links"))
    if missing:
        await interaction.response.send_message(
            f"I can't post in {target.mention}: missing **{', '.join(missing)}**. "
            "Give me those permissions there and try again.",
            ephemeral=True,
        )
        return
    async with storage.lock:
        for feature in features:
            storage.set_daily_channel(interaction.guild_id, feature, target.id)  # type: ignore[arg-type]
        storage.save()
    lines = [f"{DAILY_LABELS[f]} will be posted in {target.mention} every day at {post_time(f)}." for f in features]
    if game.CROSSWORD in features or game.WORDSEARCH in features:
        no_react = missing_perms(target, me, ("add_reactions", "read_message_history"))
        if no_react:
            lines.append(f"⚠️ I'm missing **{', '.join(no_react)}** there, so I can't react ✅/❌ to answers.")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


def _make_set_daily(feature: str) -> app_commands.Command:
    @app_commands.command(name=f"set-daily-{feature}", description=f"Post the {DAILY_LABELS[feature].split(' ', 1)[1]} here "
                          "(or in the channel you pick)")
    @app_commands.describe(channel="Channel to post in (default: this channel)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def set_daily_cmd(interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        await set_daily(interaction, (feature,), channel)

    return set_daily_cmd


for _feature in DAILY_FEATURES:
    client.tree.add_command(_make_set_daily(_feature))


@client.tree.command(name="unset-daily", description="Stop a daily post in this server")
@app_commands.describe(feature="Which daily post to turn off")
@app_commands.choices(feature=[
    app_commands.Choice(name="Question of the Day", value="qotd"),
    app_commands.Choice(name="Crossword", value=game.CROSSWORD),
    app_commands.Choice(name="Word Search", value=game.WORDSEARCH),
    app_commands.Choice(name="AREDL Hangman", value="aredl"),
    app_commands.Choice(name="All of them", value="all"),
])
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def unset_daily_cmd(interaction: discord.Interaction, feature: app_commands.Choice[str]) -> None:
    features = DAILY_FEATURES if feature.value == "all" else (feature.value,)
    async with storage.lock:
        for f in features:
            storage.set_daily_channel(interaction.guild_id, f, None)  # type: ignore[arg-type]
        storage.save()
    await interaction.response.send_message(
        "\n".join(f"{DAILY_LABELS[f]} won't be posted in this server anymore. "
                  f"Use `/set-daily-{f}` to turn it back on." for f in features),
        ephemeral=True,
    )


@client.tree.command(name="daily-channels", description="Where each daily post goes in this server")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def daily_channels_cmd(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    explicit = storage.guild_daily_channels(interaction.guild_id)  # type: ignore[arg-type]
    embed = discord.Embed(title="📅 Daily channels", color=discord.Color.blurple())
    for feature in DAILY_FEATURES:
        own = explicit.get(feature)
        # Same lookup as the daily loops, limited to this server's channels.
        here = sorted(cid for cid in daily_channel_ids(feature)
                      if cid == own or guild.get_channel_or_thread(cid) is not None)  # type: ignore[union-attr]
        if here:
            where = ", ".join(f"<#{cid}>" + (" (DAILY_CHANNEL_ID)" if cid == DEFAULT_CHANNEL_ID else "")
                              for cid in here)
        elif feature in explicit and own is None:
            where = "off"
        else:
            where = "not set"
        embed.add_field(name=DAILY_LABELS[feature], value=f"{where} • {post_time(feature)}", inline=False)
    embed.set_footer(text="Change with /set-daily-<feature> or /unset-daily")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.tree.command(name="setchannel", description="Send all daily posts (question, puzzles, hangman) to this channel")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def setchannel_cmd(interaction: discord.Interaction) -> None:
    await set_daily(interaction, DAILY_FEATURES, None)


@client.tree.command(name="postnow", description="Post today's question and puzzles here right now")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def postnow_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Posting today's daily content…", ephemeral=True)
    await post_daily(interaction.channel)  # type: ignore[arg-type]


hangman_group = app_commands.Group(
    name="hangman", description="AREDL hangman: guess the Extreme Demon (new level every 12 hours)", guild_only=True
)
NOT_LOADED = "This round's puzzle isn't loaded yet. Run `/hangman start` first."


@hangman_group.command(name="start", description="Show the current AREDL hangman puzzle")
async def hangman_start_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer()  # the level list is ~1 MB, which can take over 3 s
    if not await hangman_ready():
        await interaction.followup.send("Couldn't reach the AREDL API right now. Try again in a bit.")
        return
    g = hangman.game_for(storage.data["hangman"], interaction.guild_id)  # type: ignore[arg-type]
    note = None
    if g.finished:
        note = ("This puzzle is finished. The next one starts "
                f"{discord.utils.format_dt(hangman.next_reset(), 'R')}.")
    msg = await interaction.followup.send(embed=hangman_embed(g, note), wait=True)
    await remember_hangman(g, msg)


@hangman_group.command(name="guess", description="Guess a letter or the full level name")
@app_commands.describe(guess="A single letter/digit, or the full level name")
async def hangman_guess_cmd(interaction: discord.Interaction, guess: str) -> None:
    data = storage.data["hangman"]
    if not hangman.current(data, hangman.current_round()):
        await interaction.response.send_message(NOT_LOADED, ephemeral=True)
        return
    g = hangman.game_for(data, interaction.guild_id)  # type: ignore[arg-type]
    async with storage.lock:
        r = g.guess(interaction.user.id, guess)
        storage.save()
    text, public = hangman_note(interaction.user, r, g)
    if not public:
        await interaction.response.send_message(text, ephemeral=True)
        return
    await interaction.response.send_message(embed=hangman_embed(g, text))
    await remember_hangman(g, await interaction.original_response())
    if r.outcome == hangman.WIN and (video := await hangman_video_text(g)):
        await interaction.followup.send(video)


@hangman_group.command(name="giveup", description="Give up this puzzle and see the answer (only you see it)")
async def hangman_giveup_cmd(interaction: discord.Interaction) -> None:
    data = storage.data["hangman"]
    if not hangman.current(data, hangman.current_round()):
        await interaction.response.send_message(NOT_LOADED, ephemeral=True)
        return
    g = hangman.game_for(data, interaction.guild_id)  # type: ignore[arg-type]
    later = f"The next one starts {discord.utils.format_dt(hangman.next_reset(), 'R')}."
    async with storage.lock:
        gave_up = g.give_up(interaction.user.id)
        storage.save()
    if not gave_up:
        await interaction.response.send_message(f"This puzzle is finished. {later}", ephemeral=True)
        return
    await interaction.response.send_message(
        f"The level was **{discord.utils.escape_markdown(g.name)}** (#{g.position}). You're out for this round. {later}",
        ephemeral=True,
    )


client.tree.add_command(hangman_group)


# --------------------------------------------------------------------------- #
# AREDL RNG: a random level spawns every hour; the first to type "collect" keeps it
# --------------------------------------------------------------------------- #
def rarity_of(spawn: dict) -> aredl_rng.Rarity:
    return aredl_rng.BY_KEY.get(spawn.get("rarity", ""), aredl_rng.RARITIES[-1])


def next_hour() -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc)
    return now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)


def rng_embed(spawn: dict, escaped: bool = False) -> discord.Embed:
    rarity = rarity_of(spawn)
    name = discord.utils.escape_markdown(spawn["name"])
    claimed = spawn.get("claimed_by")
    if claimed:
        title = f"{rarity.emoji} {spawn['name']} was collected!"
        status = f"Collected by <@{claimed}>."
    elif escaped:
        title = f"💨 {spawn['name']} got away…"
        status = "Nobody collected it in time."
    else:
        title = f"{rarity.emoji} A wild {rarity.name} level appeared!"
        status = (f"Type **`{aredl_rng.COLLECT_WORD}`** to add it to your inventory! "
                  f"Next spawn {discord.utils.format_dt(next_hour(), 'R')}.")
    embed = discord.Embed(
        title=title[:256],
        description=f"## {name}\n**#{spawn['position']}** on the AREDL\n\n{status}",
        color=discord.Color.light_grey() if escaped else rarity.color,
    )
    embed.add_field(name="Rarity", value=f"{rarity.emoji} **{rarity.name}**\n"
                    f"{aredl_rng.one_in(spawn.get('chance') or 0)} spawns", inline=True)
    if spawn.get("level_id"):
        embed.add_field(name="Level ID", value=f"`{spawn['level_id']}`" + (" • 2P" if spawn.get("two_player") else ""),
                        inline=True)
    if isinstance(spawn.get("edel_enjoyment"), (int, float)):
        embed.add_field(name="Enjoyment (EDEL)", value=f"{spawn['edel_enjoyment']:.2f}", inline=True)
    if isinstance(spawn.get("gddl_tier"), (int, float)):
        embed.add_field(name="GDDL tier", value=f"Tier {round(spawn['gddl_tier'])}", inline=True)
    if spawn.get("nlw_tier"):
        embed.add_field(name="NLW tier", value=str(spawn["nlw_tier"])[:1024], inline=True)
    if spawn.get("tags"):
        embed.add_field(name="Tags", value=", ".join(spawn["tags"])[:1024], inline=False)
    if spawn.get("video"):
        embed.add_field(name="Verification", value=f"[Watch on video]({spawn['video']})", inline=False)
    if spawn.get("thumbnail"):
        embed.set_image(url=spawn["thumbnail"])
    embed.set_footer(text="AREDL RNG • levels via aredl.net • higher-ranked levels are rarer")
    return embed


async def edit_spawn_message(spawn: dict, escaped: bool = False) -> None:
    channel = client.get_channel(spawn.get("channel_id") or 0)
    if channel is None or not spawn.get("message_id"):
        return
    try:
        await channel.get_partial_message(spawn["message_id"]).edit(  # type: ignore[union-attr]
            content=None, embed=rng_embed(spawn, escaped=escaped))
    except (discord.HTTPException, AttributeError) as exc:
        log.warning("Couldn't update AREDL RNG spawn message %s: %s", spawn.get("message_id"), exc)


async def rng_spawn(guild_id: int, channel: discord.abc.Messageable) -> bool:
    """Post a new random level in ``channel``. The previous unclaimed spawn gets away."""
    spawn = aredl_rng.pick(await hangman.fetch_levels())
    if spawn is None:
        log.warning("No AREDL levels available for an RNG spawn")
        return False
    spawn["video"] = await hangman.fetch_video(spawn["id"])
    spawn["thumbnail"] = aredl_rng.youtube_thumbnail(spawn["video"])
    spawn["spawned_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    rarity = rarity_of(spawn)
    shout = None
    if rarity.key in ("mythical", "legendary"):
        shout = f"{rarity.emoji}{rarity.emoji}{rarity.emoji} **{rarity.name.upper()} SPAWN!** {rarity.emoji}{rarity.emoji}{rarity.emoji}"
    msg = await channel.send(shout, embed=rng_embed(spawn))
    spawn["channel_id"], spawn["message_id"] = msg.channel.id, msg.id
    async with storage.lock:
        old = storage.data["rng"]["spawns"].get(str(guild_id))
        storage.data["rng"]["spawns"][str(guild_id)] = spawn
        storage.save()
    if old and not old.get("claimed_by"):
        await edit_spawn_message(old, escaped=True)
    return True


async def handle_collect(message: discord.Message) -> bool:
    """``collect`` in the channel of this server's current spawn. Returns True when handled."""
    if not aredl_rng.is_collect(message.content):
        return False
    gid = str(message.guild.id)  # type: ignore[union-attr]
    spawn = storage.data["rng"]["spawns"].get(gid)
    if not spawn or spawn.get("channel_id") != message.channel.id:
        return False
    if not allow_guess(message):
        return True
    now = dt.datetime.now(dt.timezone.utc)
    async with storage.lock:
        spawn = storage.data["rng"]["spawns"].get(gid)
        won = aredl_rng.is_open(spawn, now) and spawn.get("channel_id") == message.channel.id  # type: ignore[union-attr]
        if won:
            spawn["claimed_by"] = message.author.id  # type: ignore[index]
            count = aredl_rng.add_to_inventory(storage.inventory(message.author.id), spawn, now.isoformat())  # type: ignore[arg-type]
            storage.save()
    if not won:
        await safe_react(message, "💨")  # already collected (or got away)
        return True
    rarity = rarity_of(spawn)  # type: ignore[arg-type]
    owned = "It's new in your inventory!" if count == 1 else f"You now have **{count}** of these."
    text = (f"{rarity.emoji} {message.author.mention} collected **{discord.utils.escape_markdown(spawn['name'])}** "  # type: ignore[index]
            f"(#{spawn['position']}), a **{rarity.name}** level! {owned}")  # type: ignore[index]
    try:
        await message.reply(text, mention_author=False)
    except discord.HTTPException as exc:
        log.warning("Couldn't reply to a collect in #%s: %s", message.channel, exc)
    await edit_spawn_message(spawn)  # type: ignore[arg-type]
    return True


@tasks.loop(time=[dt.time(hour=h, minute=0, second=5, tzinfo=dt.timezone.utc) for h in range(24)])
async def rng_hourly() -> None:
    for gid, channel_id in list(storage.data["rng"]["channels"].items()):
        channel = await resolve_channel(channel_id)
        if channel is None:
            continue
        try:
            await rng_spawn(int(gid), channel)
        except discord.HTTPException:
            log.exception("Failed to post an AREDL RNG spawn to %s", channel_id)


@rng_hourly.before_loop
async def _wait_ready_rng() -> None:
    await client.wait_until_ready()


@client.tree.command(name="set-aredl-rng", description="Spawn a random AREDL level here every hour (or in the channel you pick)")
@app_commands.describe(channel="Channel for the spawns (default: this channel)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def set_aredl_rng_cmd(interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
    target = channel or interaction.channel
    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("Spawns need a text channel or a thread.", ephemeral=True)
        return
    send = "send_messages_in_threads" if isinstance(target, discord.Thread) else "send_messages"
    missing = missing_perms(target, interaction.guild.me, ("view_channel", send, "embed_links"))  # type: ignore[union-attr]
    if missing:
        await interaction.response.send_message(
            f"I can't post in {target.mention}: missing **{', '.join(missing)}**. "
            "Give me those permissions there and try again.", ephemeral=True)
        return
    async with storage.lock:
        storage.data["rng"]["channels"][str(interaction.guild_id)] = target.id
        storage.save()
    await interaction.response.send_message(
        f"🎰 A random AREDL level will spawn in {target.mention} every hour, on the hour. "
        f"The first person to type `{aredl_rng.COLLECT_WORD}` keeps it. The first one is spawning now!",
        ephemeral=True)
    try:
        await rng_spawn(interaction.guild_id, target)  # type: ignore[arg-type]
    except discord.HTTPException:
        log.exception("Failed to post the first AREDL RNG spawn to %s", target.id)


@client.tree.command(name="unset-aredl-rng", description="Stop the hourly AREDL RNG spawns in this server")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def unset_aredl_rng_cmd(interaction: discord.Interaction) -> None:
    async with storage.lock:
        removed = storage.data["rng"]["channels"].pop(str(interaction.guild_id), None)
        storage.save()
    await interaction.response.send_message(
        "AREDL RNG spawns are off in this server. Inventories are kept." if removed
        else "AREDL RNG spawns weren't on in this server.", ephemeral=True)


@client.tree.command(name="inventory", description="The AREDL levels you (or someone else) collected")
@app_commands.describe(user="Whose inventory to show (default: yours)", page="Page number")
async def inventory_cmd(interaction: discord.Interaction, user: discord.User | None = None,
                        page: app_commands.Range[int, 1] = 1) -> None:
    who = user or interaction.user
    entries = aredl_rng.sorted_inventory(storage.data["inventories"].get(str(who.id), {}))
    embed = discord.Embed(title=f"🎒 {who.display_name}'s AREDL inventory", color=discord.Color.blurple())
    if not entries:
        embed.description = ("Nothing collected yet. Watch the AREDL RNG channel and type "
                             f"`{aredl_rng.COLLECT_WORD}` when a level spawns!")
        await interaction.response.send_message(embed=embed)
        return
    counts = aredl_rng.rarity_counts(storage.data["inventories"][str(who.id)])
    summary = "  ".join(f"{r.emoji} {counts[r.key]}" for r in aredl_rng.RARITIES)
    pages = math.ceil(len(entries) / aredl_rng.PAGE_SIZE)
    page = min(page, pages)
    start = (page - 1) * aredl_rng.PAGE_SIZE
    lines = []
    for e in entries[start:start + aredl_rng.PAGE_SIZE]:
        rarity = aredl_rng.BY_KEY.get(e.get("rarity", ""), aredl_rng.RARITIES[-1])
        extra = f" ×{e['count']}" if e.get("count", 1) > 1 else ""
        lines.append(f"{rarity.emoji} `#{e.get('position', '?'):>4}` {discord.utils.escape_markdown(e['name'])}{extra}")
    embed.description = f"{summary}\n\n" + "\n".join(lines)
    total = sum(e.get("count", 1) for e in entries)
    embed.set_footer(text=f"{len(entries)} unique • {total} collected • page {page}/{pages} • "
                          "positions are from when each level was collected")
    await interaction.response.send_message(embed=embed)


@client.tree.command(name="rarities", description="AREDL RNG rarities and the chance of each per spawn")
async def rarities_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    levels = await hangman.fetch_levels()
    odds = aredl_rng.tier_odds(levels) if levels else {}
    lines, low = [], 1
    for r in aredl_rng.RARITIES:
        span = f"#{low}–{r.max_position}" if r.max_position else f"#{low}+"
        chance = f" • {aredl_rng.one_in(odds[r.key])} spawns" if odds.get(r.key) else ""
        lines.append(f"{r.emoji} **{r.name}** ({span}){chance}")
        low = (r.max_position or 0) + 1
    embed = discord.Embed(title="🎰 AREDL RNG rarities", description="\n".join(lines), color=discord.Color.gold())
    embed.set_footer(text="Within a rarity, higher-ranked levels are rarer still (#1 is far rarer than #10)")
    await interaction.followup.send(embed=embed)


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
