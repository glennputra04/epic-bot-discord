"""Question of the Day.

Fetches a multiple-choice trivia question from the free Open Trivia DB API
(https://opentdb.com, no API key required). If the API is unreachable a
built-in fallback question is used so the daily post never fails.
"""

from __future__ import annotations

import html
import logging
import random

import aiohttp

log = logging.getLogger(__name__)

API_URL = "https://opentdb.com/api.php"
LETTERS = "ABCD"

FALLBACK_QUESTIONS = [
    {"category": "Science", "difficulty": "easy", "question": "What planet is known as the Red Planet?",
     "correct": "Mars", "incorrect": ["Venus", "Jupiter", "Saturn"]},
    {"category": "Geography", "difficulty": "easy", "question": "What is the largest ocean on Earth?",
     "correct": "Pacific Ocean", "incorrect": ["Atlantic Ocean", "Indian Ocean", "Arctic Ocean"]},
    {"category": "Computers", "difficulty": "medium", "question": "In what year was Python first released?",
     "correct": "1991", "incorrect": ["1985", "1995", "2000"]},
    {"category": "History", "difficulty": "medium", "question": "Who was the first person to walk on the Moon?",
     "correct": "Neil Armstrong", "incorrect": ["Buzz Aldrin", "Yuri Gagarin", "Michael Collins"]},
    {"category": "Science", "difficulty": "easy", "question": "What is the chemical symbol for gold?",
     "correct": "Au", "incorrect": ["Ag", "Gd", "Go"]},
    {"category": "Animals", "difficulty": "easy", "question": "How many legs does a spider have?",
     "correct": "8", "incorrect": ["6", "10", "12"]},
    {"category": "Art", "difficulty": "easy", "question": "Who painted the Mona Lisa?",
     "correct": "Leonardo da Vinci", "incorrect": ["Michelangelo", "Raphael", "Vincent van Gogh"]},
]


def _build(day: str, category: str, difficulty: str, question: str, correct: str, incorrect: list[str]) -> dict:
    options = [correct, *incorrect]
    random.Random(f"qotd-{day}").shuffle(options)
    return {
        "day": day,
        "category": category,
        "difficulty": difficulty,
        "question": question,
        "options": options,
        "answer_index": options.index(correct),
    }


async def fetch_question(day: str, category: int | None = None) -> dict:
    """Return the question for ``day``. ``category`` is an optional Open Trivia DB category id."""
    params = {"amount": "1", "type": "multiple"}
    if category:
        params["category"] = str(category)
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(API_URL, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("response_code") != 0 or not data.get("results"):
            raise ValueError(f"Unexpected API response: {data.get('response_code')}")
        item = data["results"][0]
        return _build(
            day,
            html.unescape(item["category"]),
            item["difficulty"],
            html.unescape(item["question"]),
            html.unescape(item["correct_answer"]),
            [html.unescape(a) for a in item["incorrect_answers"]],
        )
    except Exception as exc:  # network errors, rate limits, bad JSON...
        log.warning("Open Trivia DB request failed (%s); using fallback question", exc)
        fb = random.Random(day).choice(FALLBACK_QUESTIONS)
        return _build(day, fb["category"], fb["difficulty"], fb["question"], fb["correct"], fb["incorrect"])
