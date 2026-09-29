"""Local, JSON-backed storage for flashcards, quiz history and exam info.

Flashcards use a Leitner spaced-repetition system: a card starts in box 1 and
moves up one box each time you get it right (so you see it less often) and
drops back to box 1 when you get it wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("America/New_York")

# Days until a card in box N is due again after a correct answer.
BOX_INTERVAL_DAYS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 14}
MAX_BOX = max(BOX_INTERVAL_DAYS)


def now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


@dataclass
class TopicStats:
    correct: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.total if self.total else None


class StudyStore:
    """Persists study progress to ``<data_dir>/progress.json``."""

    def __init__(self, data_dir: Path | str, clock=now):
        self.path = Path(data_dir) / "progress.json"
        self._clock = clock
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {"exam": None, "flashcards": [], "quiz_log": [], "next_id": 1}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)

    # ----- exam -------------------------------------------------------------

    def set_exam(self, name: str, exam_date: str) -> dict:
        date.fromisoformat(exam_date)  # raises ValueError on a bad date
        self.data["exam"] = {"name": name, "date": exam_date}
        self.save()
        return self.data["exam"]

    def status(self) -> dict:
        local_now = self._clock().astimezone(TIMEZONE)
        exam = self.data["exam"]
        days_left = None
        if exam:
            days_left = (date.fromisoformat(exam["date"]) - local_now.date()).days
        return {
            "now": local_now.strftime("%A, %B %d, %Y %I:%M %p %Z"),
            "exam": exam,
            "days_until_exam": days_left,
            "flashcards_total": len(self.data["flashcards"]),
            "flashcards_due": len(self.due_flashcards(limit=None)),
            "quiz_questions_answered": len(self.data["quiz_log"]),
        }

    # ----- flashcards -------------------------------------------------------

    def add_flashcards(self, cards: list[dict]) -> list[dict]:
        created = []
        ts = self._clock().isoformat()
        for card in cards:
            new = {
                "id": self.data["next_id"],
                "front": card["front"].strip(),
                "back": card["back"].strip(),
                "topic": card["topic"].strip(),
                "box": 1,
                "due": ts,
                "reviews": 0,
                "lapses": 0,
            }
            self.data["next_id"] += 1
            self.data["flashcards"].append(new)
            created.append(new)
        self.save()
        return created

    def due_flashcards(self, limit: int | None = 10, topic: str | None = None) -> list[dict]:
        current = self._clock()
        due = [
            c
            for c in self.data["flashcards"]
            if _parse(c["due"]) <= current
            and (topic is None or c["topic"].lower() == topic.lower())
        ]
        # Lowest box first (the cards you know least), then oldest due date.
        due.sort(key=lambda c: (c["box"], c["due"]))
        return due if limit is None else due[:limit]

    def review_flashcard(self, card_id: int, correct: bool) -> dict:
        card = next((c for c in self.data["flashcards"] if c["id"] == card_id), None)
        if card is None:
            raise KeyError(f"No flashcard with id {card_id}")
        card["reviews"] += 1
        if correct:
            card["box"] = min(card["box"] + 1, MAX_BOX)
        else:
            card["box"] = 1
            card["lapses"] += 1
        card["due"] = (self._clock() + timedelta(days=BOX_INTERVAL_DAYS[card["box"]])).isoformat()
        self.save()
        return card

    # ----- quizzes ----------------------------------------------------------

    def record_quiz_answer(self, topic: str, question: str, correct: bool) -> dict:
        entry = {
            "topic": topic.strip(),
            "question": question.strip(),
            "correct": correct,
            "ts": self._clock().isoformat(),
        }
        self.data["quiz_log"].append(entry)
        self.save()
        return entry

    # ----- reporting --------------------------------------------------------

    def progress_report(self, recent_misses: int = 10) -> dict:
        topics: dict[str, TopicStats] = {}
        for entry in self.data["quiz_log"]:
            stats = topics.setdefault(entry["topic"], TopicStats())
            stats.total += 1
            stats.correct += entry["correct"]
        for card in self.data["flashcards"]:
            stats = topics.setdefault(card["topic"], TopicStats())
            stats.total += card["reviews"]
            stats.correct += card["reviews"] - card["lapses"]

        by_topic = {
            name: {
                "answered": s.total,
                "correct": s.correct,
                "accuracy": round(s.accuracy, 2) if s.accuracy is not None else None,
            }
            for name, s in sorted(topics.items())
        }
        weakest = sorted(
            (name for name, s in topics.items() if s.total),
            key=lambda name: (topics[name].accuracy, -topics[name].total),
        )[:3]
        misses = [e for e in self.data["quiz_log"] if not e["correct"]][-recent_misses:]
        mastered = sum(1 for c in self.data["flashcards"] if c["box"] >= 4)
        return {
            "by_topic": by_topic,
            "weakest_topics": weakest,
            "recent_quiz_misses": misses,
            "flashcards_mastered": mastered,
            "flashcards_total": len(self.data["flashcards"]),
        }
