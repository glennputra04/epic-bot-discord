# Epic Bot Discord

A Python Discord bot that posts three things every day:

- **💬 Question of the Day:** an open-ended question from the [boneitis.org API](https://boneitis.org/docs). There's no answer to check; it's just a conversation starter.
- **🧩 Daily Crossword:** a crossword built from `words.json`, with a **Play on Puzzel** link from the [Puzzel.org Puzzle API](https://puzzel.org/en/developers/puzzle-api).
- **🔎 Daily Word Search:** an 8-word search built from the same list, also with a Puzzel link. Players get each word's clue but not the word itself, so they work out the word and then find it in the grid.

Every date always gives the same puzzles, so the whole server solves the same ones.

## Answering

Type answers in the daily channel. The bot reacts ✅ if you're right and ❌ if you're wrong. Uppercase and lowercase don't matter.

| Message | Meaning |
|---|---|
| `1. apple` / `1 apple` | Clue 1 in the crossword (across or down) or word 1 in the word search |
| `3 down banana` / `3d banana` / `2 across: kiwi` | A specific crossword direction |
| `4. \|\|otter\|\|` | Spoiler tags work, so others don't see the answer |

If you **reply** to the crossword or word search post, the bot only checks against that puzzle. Messages that don't look like an answer (e.g. `10 minutes until lunch`) are ignored. Each correct, new word earns 1 point, and finishing a puzzle gets announced.

## Commands

| Command | Description |
|---|---|
| `/qotd` | Today's question |
| `/crossword` | Today's crossword |
| `/wordsearch` | Today's word search |
| `/progress` | Your solved clues and found words (only you see it) |
| `/yesterday` | Yesterday's crossword and word search solutions |
| `/leaderboard` | Top 10 solvers |
| `/setchannel` | *(Manage Server)* Send daily posts to this channel |
| `/postnow` | *(Manage Server)* Post today's content here right away |

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

5. In your server, run `/setchannel` in the channel you want, then `/postnow` to test.

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
words.json     Word/clue bank
storage.py     JSON file storage (data/state.json)
```
