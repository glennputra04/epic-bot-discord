"""Daily Discord bot: Question of the Day + Daily Crossword."""

from __future__ import annotations

import datetime as dt
import logging
import os
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

import crossword
from qotd import LETTERS, fetch_question
from storage import Storage

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dailybot")

TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.getenv("GUILD_ID") or 0) or None
DEFAULT_CHANNEL_ID = int(os.getenv("DAILY_CHANNEL_ID") or 0) or None
TZ = ZoneInfo(os.getenv("TIMEZONE", "UTC"))
POST_HOUR, POST_MINUTE = (int(x) for x in os.getenv("POST_TIME", "09:00").split(":"))
TRIVIA_CATEGORY = int(os.getenv("TRIVIA_CATEGORY") or 0) or None
DATA_FILE = os.getenv("DATA_FILE", "data/state.json")

CROSSWORD_POINTS = 3
storage = Storage(DATA_FILE)


def today() -> str:
    return dt.datetime.now(TZ).date().isoformat()


def yesterday() -> str:
    return (dt.datetime.now(TZ).date() - dt.timedelta(days=1)).isoformat()


@lru_cache(maxsize=8)
def puzzle_for(day: str) -> crossword.Crossword:
    return crossword.generate(day)


async def question_for(day: str) -> dict:
    """Fetch the day's question once, then serve it from storage."""
    async with storage.lock:
        q = storage.data["questions"].get(day)
        if q is None:
            q = await fetch_question(day, TRIVIA_CATEGORY)
            storage.data["questions"][day] = q
            storage.save()
    return q


# --------------------------------------------------------------------------- #
# Embeds
# --------------------------------------------------------------------------- #
def qotd_embed(q: dict) -> discord.Embed:
    options = "\n".join(f"**{LETTERS[i]}.** {opt}" for i, opt in enumerate(q["options"]))
    embed = discord.Embed(
        title=f"❓ Question of the Day — {q['day']}",
        description=f"**{q['question']}**\n\n{options}",
        color=discord.Color.blurple(),
    )
    answered = len(storage.data["qotd_answers"].get(q["day"], {}))
    embed.set_footer(text=f"{q['category']} • {q['difficulty'].title()} • Click a button to answer (one try!)")
    if answered:
        embed.add_field(name="Answers so far", value=str(answered))
    return embed


def crossword_embed(p: crossword.Crossword, solved: set[str] | None = None, reveal: bool = False) -> discord.Embed:
    title = f"🧩 Daily Crossword — {p.day}" + (" (solution)" if reveal else "")
    embed = discord.Embed(
        title=title,
        description=f"```\n{p.render(solved, reveal)}\n```\n{p.clues_text()}",
        color=discord.Color.green() if reveal else discord.Color.orange(),
    )
    if not reveal:
        embed.set_footer(text="Solve with /solve <number> <across|down> <answer> • Check progress with /progress")
    return embed


# --------------------------------------------------------------------------- #
# QOTD answer buttons (persistent across restarts via DynamicItem)
# --------------------------------------------------------------------------- #
class AnswerButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"qotd:(?P<day>\d{4}-\d{2}-\d{2}):(?P<idx>\d)",
):
    def __init__(self, day: str, idx: int):
        super().__init__(
            discord.ui.Button(
                label=LETTERS[idx],
                style=discord.ButtonStyle.primary,
                custom_id=f"qotd:{day}:{idx}",
            )
        )
        self.day = day
        self.idx = idx

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["day"], int(match["idx"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        q = storage.data["questions"].get(self.day)
        if q is None:
            await interaction.response.send_message("I can't find that question anymore.", ephemeral=True)
            return
        correct_idx = q["answer_index"]
        correct_text = f"**{LETTERS[correct_idx]}. {q['options'][correct_idx]}**"

        if self.day != today():
            await interaction.response.send_message(
                f"This question is closed. The answer was {correct_text}.", ephemeral=True
            )
            return

        uid = str(interaction.user.id)
        async with storage.lock:
            answers = storage.data["qotd_answers"].setdefault(self.day, {})
            if uid in answers:
                prev = answers[uid]
                await interaction.response.send_message(
                    f"You already answered **{LETTERS[prev]}** today. Come back tomorrow!", ephemeral=True
                )
                return
            answers[uid] = self.idx
            if self.idx == correct_idx:
                storage.score(interaction.user.id)["qotd"] += 1
            storage.save()

        if self.idx == correct_idx:
            msg = f"✅ Correct! The answer is {correct_text}."
        else:
            msg = f"❌ Not quite — the answer was {correct_text}."
        await interaction.response.send_message(msg, ephemeral=True)


def qotd_view(q: dict) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for i in range(len(q["options"])):
        view.add_item(AnswerButton(q["day"], i))
    return view


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class DailyBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        self.add_dynamic_items(AnswerButton)
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        daily_post.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s); daily post at %02d:%02d %s",
                 self.user, self.user.id if self.user else "?", POST_HOUR, POST_MINUTE, TZ.key)


client = DailyBot()


def daily_channel_ids() -> set[int]:
    ids = {int(s["channel_id"]) for s in storage.data["settings"].values() if s.get("channel_id")}
    if DEFAULT_CHANNEL_ID:
        ids.add(DEFAULT_CHANNEL_ID)
    return ids


async def post_daily(channel: discord.abc.Messageable) -> None:
    day, prev = today(), yesterday()

    prev_q = storage.data["questions"].get(prev)
    if prev_q:
        idx = prev_q["answer_index"]
        await channel.send(
            f"📢 Yesterday's question: *{prev_q['question']}*\n"
            f"Answer: **{LETTERS[idx]}. {prev_q['options'][idx]}**"
        )

    q = await question_for(day)
    await channel.send(embed=qotd_embed(q), view=qotd_view(q))
    await channel.send(embed=crossword_embed(puzzle_for(day)))


@tasks.loop(time=dt.time(hour=POST_HOUR, minute=POST_MINUTE, tzinfo=TZ))
async def daily_post() -> None:
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
    q = await question_for(today())
    await interaction.followup.send(embed=qotd_embed(q), view=qotd_view(q))


@client.tree.command(name="crossword", description="Show today's crossword puzzle")
async def crossword_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(embed=crossword_embed(puzzle_for(today())))


@client.tree.command(name="solve", description="Submit an answer for today's crossword")
@app_commands.describe(number="Clue number", direction="Across or down", answer="Your answer")
async def solve_cmd(
    interaction: discord.Interaction,
    number: app_commands.Range[int, 1, 99],
    direction: Literal["across", "down"],
    answer: str,
) -> None:
    day = today()
    p = puzzle_for(day)
    entry = p.find(number, direction)
    if entry is None:
        await interaction.response.send_message(f"There's no **{number} {direction}** today.", ephemeral=True)
        return

    guess = "".join(ch for ch in answer.upper() if ch.isalpha())
    uid = str(interaction.user.id)
    async with storage.lock:
        solved = storage.data["crossword"].setdefault(day, {}).setdefault(uid, [])
        if entry.key in solved:
            await interaction.response.send_message("You've already solved that one!", ephemeral=True)
            return
        if guess != entry.answer:
            await interaction.response.send_message(
                f"❌ **{guess or answer}** isn't right for {number} {direction} ({len(entry.answer)} letters).",
                ephemeral=True,
            )
            return
        solved.append(entry.key)
        finished = len(solved) == len(p.entries)
        if finished:
            storage.score(interaction.user.id)["crosswords"] += 1
        storage.save()

    await interaction.response.send_message(
        f"✅ **{entry.answer}** is correct! ({len(solved)}/{len(p.entries)})\n```\n{p.render(set(solved))}\n```",
        ephemeral=True,
    )
    if finished:
        await interaction.followup.send(f"🎉 {interaction.user.mention} completed today's crossword!")


@client.tree.command(name="progress", description="See your progress on today's crossword (only visible to you)")
async def progress_cmd(interaction: discord.Interaction) -> None:
    day = today()
    p = puzzle_for(day)
    solved = set(storage.data["crossword"].get(day, {}).get(str(interaction.user.id), []))
    embed = crossword_embed(p, solved)
    embed.add_field(name="Solved", value=f"{len(solved)}/{len(p.entries)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.tree.command(name="yesterday", description="Reveal yesterday's QOTD answer and crossword solution")
async def yesterday_cmd(interaction: discord.Interaction) -> None:
    day = yesterday()
    embeds = []
    q = storage.data["questions"].get(day)
    if q:
        idx = q["answer_index"]
        embeds.append(discord.Embed(
            title=f"❓ QOTD answer — {day}",
            description=f"*{q['question']}*\n\n**{LETTERS[idx]}. {q['options'][idx]}**",
            color=discord.Color.blurple(),
        ))
    embeds.append(crossword_embed(puzzle_for(day), reveal=True))
    await interaction.response.send_message(embeds=embeds)


@client.tree.command(name="leaderboard", description="Top players for QOTD and crosswords")
async def leaderboard_cmd(interaction: discord.Interaction) -> None:
    scores = storage.data["scores"]
    ranked = sorted(
        scores.items(),
        key=lambda kv: kv[1]["qotd"] + CROSSWORD_POINTS * kv[1]["crosswords"],
        reverse=True,
    )[:10]
    if not ranked:
        await interaction.response.send_message("No scores yet — answer a question or solve a crossword!")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"{medals[i] if i < 3 else f'`{i + 1}.`'} <@{uid}> — "
        f"**{s['qotd'] + CROSSWORD_POINTS * s['crosswords']}** pts "
        f"({s['qotd']} correct QOTD, {s['crosswords']} crosswords)"
        for i, (uid, s) in enumerate(ranked)
    ]
    embed = discord.Embed(title="🏆 Leaderboard", description="\n".join(lines), color=discord.Color.gold())
    embed.set_footer(text=f"1 pt per correct QOTD, {CROSSWORD_POINTS} pts per completed crossword")
    await interaction.response.send_message(embed=embed)


@client.tree.command(name="setchannel", description="Post the daily QOTD and crossword in this channel")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def setchannel_cmd(interaction: discord.Interaction) -> None:
    async with storage.lock:
        storage.data["settings"][str(interaction.guild_id)] = {"channel_id": interaction.channel_id}
        storage.save()
    await interaction.response.send_message(
        f"Daily posts will go to <#{interaction.channel_id}> at {POST_HOUR:02d}:{POST_MINUTE:02d} ({TZ.key})."
    )


@client.tree.command(name="postnow", description="Post today's QOTD and crossword here right now")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def postnow_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Posting today's daily content…", ephemeral=True)
    await post_daily(interaction.channel)  # type: ignore[arg-type]


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
