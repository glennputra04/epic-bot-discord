# Epic Bot Discord

A Python Discord bot that posts four things every day:

- **💬 Question of the Day:** an open-ended question from the [boneitis.org API](https://boneitis.org/docs). There's no answer to check; it's just a conversation starter.
- **🧩 Daily Crossword:** a crossword built from `words.json`, with a **Play on Puzzel** link from the [Puzzel.org Puzzle API](https://puzzel.org/en/developers/puzzle-api).
- **🔎 Daily Word Search:** an 8-word search built from the same list, also with a Puzzel link. Players get each word's clue but not the word itself, so they work out the word and then find it in the grid.
- **🎯 Daily AREDL Hangman:** guess the name of a level from the [AREDL API](https://api.aredl.net/v2/docs), with its description as a hint and 6 lives per player.

Every date always gives the same puzzles, so the whole server solves the same ones.

## Answering

Type answers in the crossword or word search daily channel. The bot reacts ✅ if you're right and ❌ if you're wrong. Uppercase and lowercase don't matter.

| Message | Meaning |
|---|---|
| `1. apple` / `1 apple` | Clue 1 in the crossword (across or down) or word 1 in the word search |
| `3 down banana` / `3d banana` / `2 across: kiwi` | A specific crossword direction |
| `4. \|\|otter\|\|` | Spoiler tags work, so others don't see the answer |

If you **reply** to the crossword or word search post, the bot only checks against that puzzle. Messages that don't look like an answer (e.g. `10 minutes until lunch`) are ignored. Each correct, new word earns 1 point, and finishing a puzzle gets announced.

## Daily AREDL Hangman

One level from the [AREDL list](https://api.aredl.net/v2/docs) per UTC day. A new level starts at 00:00 UTC (from the server's system clock, not `TIMEZONE`) and is posted to the AREDL daily channel (`/set-daily-aredl`). Everyone gets the same level; each server has its own board.

```
_ _ _ _ _ _ _  /  _ _
```

- Type a **single letter or digit** in a channel where today's board was posted, **reply** to the board with the full level name, or use `/hangman guess`. Case, spaces and punctuation don't matter. In chat, repeated guesses and guesses from players who are out are ignored quietly.
- Every player has **6 lives** a day. A wrong letter or wrong name costs only you a life; at 0 you're out until tomorrow, but everyone else can keep playing. Repeating a guess costs nothing.
- The hint is the level's description with the level name blanked out. Levels without one show "No description available." and their tags. While playing, the position is shown as a band (`Top 100`, `#101–250`, `#251–500`, `#501–1000`, `#1001+` or `Legacy`); the exact position appears once the puzzle is solved.
- The puzzle stays open until someone completes the name. A solved puzzle stays solved until the next UTC day, and the next day's post in the AREDL daily channel reveals yesterday's level either way.
- `/hangman giveup` is personal: it shows you the answer (only you see it) and puts you out for the day. It doesn't end the puzzle for anyone else.

## Commands

| Command | Description |
|---|---|
| `/qotd` | Today's question |
| `/crossword` | Today's crossword |
| `/wordsearch` | Today's word search |
| `/progress` | Your solved clues and found words (only you see it) |
| `/yesterday` | Yesterday's crossword and word search solutions |
| `/leaderboard` | Top 10 solvers |
| `/hangman start` | Show today's AREDL hangman board |
| `/hangman guess` | Guess a letter/digit or the full level name |
| `/hangman giveup` | See the answer (only you see it); you're out for today |
| `/set-daily-qotd [channel]` | *(Manage Server)* Post the Question of the Day in a channel (default: this one) |
| `/set-daily-crossword [channel]` | *(Manage Server)* Post the daily crossword in a channel (default: this one) |
| `/set-daily-wordsearch [channel]` | *(Manage Server)* Post the daily word search in a channel (default: this one) |
| `/set-daily-aredl [channel]` | *(Manage Server)* Post the daily AREDL hangman in a channel (default: this one) |
| `/unset-daily` | *(Manage Server)* Turn one daily post (or all of them) off in this server |
| `/daily-channels` | *(Manage Server)* Show where each daily post goes (only you see it) |
| `/setchannel` | *(Manage Server)* Send all daily posts to this channel (shortcut for every `/set-daily-*`) |
| `/postnow` | *(Manage Server)* Post today's question, crossword and word search here right away |

## Daily channels

Each daily post goes only to its own channel, so you can split them up per server:

- Question of the Day, crossword and word search are posted at `POST_TIME` (`TIMEZONE`); the hangman at 00:00 UTC.
- `DAILY_CHANNEL_ID` in `.env` is the default for any feature a server hasn't set or turned off.
- `/unset-daily` turns a feature off for that server, including the `DAILY_CHANNEL_ID` default.
- Servers set up with the old `/setchannel` keep getting every feature in that channel after upgrading.
- Setup commands check that the bot can view the channel, send messages and embed links there; replies are only visible to you.

## Rate limits

To keep spam from flooding channels or the bot's storage:

- Slash commands are limited per user. Going over shows a "slow down, try again in Ns" note only you can see, once per cooldown.
- Chat guesses (hangman letters/names, crossword and word search answers) are limited per user and per channel. Guesses over the limit are ignored silently.
- No one is exempt, including admins. Limits are kept in memory and reset when the bot restarts.

Override the defaults in `.env` with `count/seconds`:

| Variable | Default | Limit |
|---|---|---|
| `RATELIMIT_COMMANDS` | `5/10` | Slash commands per user |
| `RATELIMIT_GUESSES` | `6/10` | Chat guesses per user |
| `RATELIMIT_CHANNEL` | `5/5` | Chat guesses the bot acts on per channel |

## Setup

1. Create an application at the [Discord Developer Portal](https://discord.com/developers/applications) and add a **Bot**. Copy its token.
2. On the **Bot** page, turn on **Message Content Intent**. Without it the bot can't read answers.
3. Invite the bot with the `bot` and `applications.commands` scopes and the Send Messages, Embed Links, Add Reactions and Read Message History permissions:
   `https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=84032`
4. Install and run (Python 3.10+):

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env             # then edit .env
   python bot.py
   ```

5. In your server, run `/setchannel` in the channel you want (or use the `/set-daily-*` commands to give each post its own channel), then `/postnow` to test.

### Puzzel links (optional)

Put your Puzzel API key and account email in `PUZZEL_API_KEY` and `PUZZEL_EMAIL`. Without them, the puzzles still work in Discord, just without the link.

- The bot creates 2 Puzzel activities a day. Puzzel's daily API limit is 10, but its free plan only allows a few activities, so daily use probably needs a paid plan.
- Answers are checked against the clue numbers in the **bot's** post. Clue numbers on Puzzel's site may differ.
- If Puzzel rejects a request, the bot logs Puzzel's error message. Check that message against the API docs and adjust `puzzel.py` or the `PUZZEL_*_PATH` settings if needed.

## Customizing

Add words to `words.json` (`{"word": "...", "clue": "..."}`, 3–10 letters). To preview puzzles in your terminal:

```bash
python crossword.py
python wordsearch.py
python hangman.py      # today's hangman level (fetches the AREDL list)
```

## Files

```
bot.py         Discord client, answer reactions, slash commands, daily scheduler
qotd.py        boneitis.org Question of the Day client
puzzel.py      Puzzel.org Puzzle API client
game.py        Today's puzzles + answer checking
answers.py     Parses messages like "1. apple" / "3 down banana"
crossword.py   Crossword generator and text renderer
wordsearch.py  Word search generator and text renderer
hangman.py     Daily AREDL hangman: API client, masking, guesses and lives
ratelimit.py   Per-user/per-channel rate limits (token bucket)
words.json     Word/clue bank
storage.py     JSON file storage (data/state.json)
```
