from datetime import datetime, timedelta, timezone

import pytest

from study_agent.store import StudyStore


class Clock:
    def __init__(self):
        # 2026-09-29 20:00 UTC is 4:00 PM in New York.
        self.t = datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, days):
        self.t += timedelta(days=days)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return StudyStore(tmp_path, clock=clock)


def test_status_uses_new_york_time_and_counts_down(store):
    store.set_exam("BIO 101 Final", "2026-10-09")
    status = store.status()
    assert "September 29, 2026 04:00 PM EDT" in status["now"]
    assert status["days_until_exam"] == 10


def test_set_exam_rejects_bad_date(store):
    with pytest.raises(ValueError):
        store.set_exam("Final", "next tuesday")


def test_leitner_scheduling(store, clock):
    [card] = store.add_flashcards([{"front": "Q", "back": "A", "topic": "Cells"}])
    assert [c["id"] for c in store.due_flashcards()] == [card["id"]]

    store.review_flashcard(card["id"], correct=True)  # box 2, due in 1 day
    assert store.due_flashcards() == []
    clock.advance(1)
    assert len(store.due_flashcards()) == 1

    store.review_flashcard(card["id"], correct=False)  # back to box 1, due now
    reviewed = store.due_flashcards()[0]
    assert reviewed["box"] == 1 and reviewed["lapses"] == 1 and reviewed["reviews"] == 2


def test_due_cards_weakest_first_and_topic_filter(store):
    a, b = store.add_flashcards(
        [
            {"front": "a", "back": "a", "topic": "Cells"},
            {"front": "b", "back": "b", "topic": "Genetics"},
        ]
    )
    store.data["flashcards"][0]["box"] = 3
    assert [c["id"] for c in store.due_flashcards()] == [b["id"], a["id"]]
    assert [c["id"] for c in store.due_flashcards(topic="cells")] == [a["id"]]


def test_progress_persists_and_reports_weakest(tmp_path, store, clock):
    store.record_quiz_answer("Cells", "What is ATP?", True)
    store.record_quiz_answer("Genetics", "Define allele", False)
    store.record_quiz_answer("Genetics", "Define locus", True)

    reloaded = StudyStore(tmp_path, clock=clock)
    report = reloaded.progress_report()
    assert report["by_topic"]["Genetics"] == {"answered": 2, "correct": 1, "accuracy": 0.5}
    assert report["weakest_topics"][0] == "Genetics"
    assert report["recent_quiz_misses"][0]["question"] == "Define allele"


def test_review_unknown_card(store):
    with pytest.raises(KeyError):
        store.review_flashcard(999, True)
