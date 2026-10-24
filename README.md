# Discord Daily Bot

A Python Discord bot that posts a **Question of the Day** and a **Daily Crossword** every day.

- **Question of the Day:** a multiple-choice trivia question from the free [Open Trivia DB](https://opentdb.com) API (no key needed). Members answer with buttons and get one try per day. If the API is down, the bot uses a built-in question instead.
- **Daily Crossword:** a small crossword generated from `words.json`. Every date always gives the same puzzle, so everyone in the server solves the same one. Members solve it privately with `/solve`.
- Points are tracked on a leaderboard, and yesterday's answers are revealed in the next daily post.

## Commands

| Command | Description |
|---|---|
| `/qotd` | Show today's question |
| `/crossword` | Show today's crossword grid and clues |
| `/solve <number> <across\|down> <answer>` | Submit a crossword answer (only you see the reply) |
| `/progress` | Your filled-in grid for today (only you see it) |
| `/yesterday` | Yesterday's QOTD answer and crossword solution |
| `/leaderboard` | Top 10: 1 pt per correct QOTD, 3 pts per finished crossword |
| `/setchannel` | *(Manage Server)* Send daily posts to the current channel |
| `/postnow` | *(Manage Server)* Post today's content here right away |

## Setup

1. Create an application at the [Discord Developer Portal](https://discord.com/developers/applications), add a **Bot**, and copy its token.
2. Invite it with the `bot` and `applications.commands` scopes and the **Send Messages** and **Embed Links** permissions:
   `https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=18432`
3. Install and run (Python 3.10+):

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env             # then edit .env and set DISCORD_TOKEN
   python bot.py
   ```

4. In your server, run `/setchannel` in the channel you want, then `/postnow` to test.

Set `GUILD_ID` in `.env` to make slash commands show up instantly. Without it, Discord can take up to an hour to show them. See `.env.example` for all settings (post time, timezone, trivia category).

## Customizing the crossword

Add entries to `words.json` (`{"word": "...", "clue": "..."}`, 3–10 letters). More words give more varied puzzles. To preview today's puzzle in your terminal:

```bash
python crossword.py
```

## Files

```
bot.py         Discord client, slash commands, daily scheduler
qotd.py        Open Trivia DB client + fallback questions
crossword.py   Crossword generator and text renderer
words.json     Crossword word/clue bank
storage.py     JSON file storage (data/state.json)
```
