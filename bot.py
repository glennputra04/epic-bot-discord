"""Daily Discord bot: Question of the Day + Daily Crossword + Daily Word Search."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()  # before importing puzzel, which reads its settings from the environment

import answers  # noqa: E402
import game  # noqa: E402
import puzzel  # noqa: E402
from qotd import fetch_question  # noqa: E402
from storage import Storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dailybot")

TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.getenv("GUILD_ID") or 0) or None
DEFAULT_CHANNEL_ID = int(os.getenv("DAILY_CHANNEL_ID") or 0) or None
TZ = ZoneInfo(os.getenv("TIMEZONE", "UTC"))
POST_HOUR, POST_MINUTE = (int(x) for x in os.getenv("POST_TIME", "09:00").split(":"))
QOTD_CATEGORY = os.getenv("QOTD_CATEGORY", "general")
DATA_FILE = os.getenv("DATA_FILE", "data/state.json")

CORRECT, WRONG = "✅", "❌"
storage = Storage(DATA_FILE)
puzzel_lock = asyncio.Lock()
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


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class DailyBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # needed to read "1. apple" answers
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        daily_post.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s; daily post at %02d:%02d %s; Puzzel %s",
                 self.user, POST_HOUR, POST_MINUTE, TZ.key, "on" if puzzel.enabled() else "off")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
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
    ids.update(int(s["channel_id"]) for s in storage.data["settings"].values() if s.get("channel_id"))
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


def daily_channel_ids() -> set[int]:
    ids = {int(s["channel_id"]) for s in storage.data["settings"].values() if s.get("channel_id")}
    if DEFAULT_CHANNEL_ID:
        ids.add(DEFAULT_CHANNEL_ID)
    return ids


@tasks.loop(time=dt.time(hour=POST_HOUR, minute=POST_MINUTE, tzinfo=TZ))
async def daily_post() -> None:
    async with storage.lock:
        storage.prune({today(), yesterday()})
        storage.save()
    for channel_id in daily_channel_ids():
        channel = client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(channel_id)
            except discord.HTTPException:
                log.warning("Can't access channel %s", channel_id)
                continue
        try:
            await post_daily(channel)  # type: ignore[arg-type]
        except discord.HTTPException:
            log.exception("Failed to post daily content to %s", channel_id)


@daily_post.before_loop
async def _wait_ready() -> None:
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


@client.tree.command(name="setchannel", description="Post the daily question and puzzles in this channel")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def setchannel_cmd(interaction: discord.Interaction) -> None:
    async with storage.lock:
        storage.data["settings"][str(interaction.guild_id)] = {"channel_id": interaction.channel_id}
        storage.save()
    await interaction.response.send_message(
        f"Daily posts will go to <#{interaction.channel_id}> at {POST_HOUR:02d}:{POST_MINUTE:02d} ({TZ.key})."
    )


@client.tree.command(name="postnow", description="Post today's question and puzzles here right now")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def postnow_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Posting today's daily content…", ephemeral=True)
    await post_daily(interaction.channel)  # type: ignore[arg-type]


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
