"""Interactive terminal study session."""

from __future__ import annotations

import argparse
import sys
import textwrap

import anthropic

from .agent import DEFAULT_MODEL, StudyAgent
from .materials import load_materials
from .store import StudyStore

HELP = """\
Just chat, or use a shortcut:
  /quiz [topic]        Quiz me, one question at a time
  /cards [topic]       Review my due flashcards
  /make-cards [topic]  Turn the materials into flashcards
  /explain <concept>   Teach me a concept step by step
  /plan                Build a day-by-day plan up to the exam
  /progress            How am I doing? What should I focus on?
  /exam <YYYY-MM-DD> <name>   Set the exam date and name
  /status              Show exam countdown and cards due (no AI call)
  /help                Show this help
  /quit                End the session
"""

STOP_NOTICES = {
    "refusal": "[Claude declined to answer that. Try rephrasing the request.]",
    "max_tokens": "[The reply hit the length limit and was cut off. Ask again with a narrower request.]",
    "too_many_tool_steps": "[That request needed too many steps. Try breaking it into smaller pieces.]",
}


def expand_shortcut(line: str) -> str | None:
    """Turn a /shortcut into the message sent to Claude (None if not a shortcut)."""
    command, _, arg = line.partition(" ")
    arg = arg.strip()
    on = f" on {arg}" if arg else ""
    if command == "/quiz":
        return (
            f"Quiz me{on or ' on the material'}. Ask one question at a time, wait for my "
            "answer, then grade it and explain."
        )
    if command == "/cards":
        return f"Let's review my due flashcards{on}. Show me one front at a time."
    if command == "/make-cards":
        return (
            f"Create a set of flashcards from my study materials{on}, focusing on what is "
            "most likely to be tested, and save them to my deck."
        )
    if command == "/explain":
        if not arg:
            return None
        return f"Explain {arg} to me step by step, then check that I understood it."
    if command == "/plan":
        return (
            "Build me a day-by-day study plan from today until my exam, based on my "
            "materials and my weak topics."
        )
    if command == "/progress":
        return "How am I doing? Summarize my progress and tell me what to focus on next."
    return None


def print_status(store: StudyStore) -> None:
    s = store.status()
    print(f"  Now: {s['now']}")
    if s["exam"]:
        days = s["days_until_exam"]
        when = "today" if days == 0 else f"in {days} days" if days > 0 else f"{-days} days ago"
        print(f"  Exam: {s['exam']['name']} on {s['exam']['date']} ({when})")
    else:
        print("  Exam: not set (use /exam YYYY-MM-DD Name)")
    print(f"  Flashcards: {s['flashcards_due']} due of {s['flashcards_total']}")
    print(f"  Quiz questions answered: {s['quiz_questions_answered']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m study_agent",
        description="An AI study coach that quizzes you, makes flashcards and plans your prep.",
    )
    parser.add_argument(
        "materials",
        nargs="*",
        help="Notes, slides or readings to study from (.md, .txt, .pdf, ... or a folder).",
    )
    parser.add_argument("--exam", help="Exam name, e.g. 'CHEM 210 Final'.")
    parser.add_argument("--exam-date", help="Exam date as YYYY-MM-DD.")
    parser.add_argument(
        "--data-dir",
        default=".study_data",
        help="Where progress and flashcards are saved (default: .study_data).",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Default: {DEFAULT_MODEL}.")
    parser.add_argument(
        "--effort",
        default="medium",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="How hard Claude thinks before answering (default: medium).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = StudyStore(args.data_dir)

    if args.exam_date:
        store.set_exam(args.exam or "Exam", args.exam_date)
    elif args.exam:
        print("Please also pass --exam-date YYYY-MM-DD.", file=sys.stderr)
        return 2

    try:
        documents, files = load_materials(args.materials)
    except (OSError, ValueError) as exc:
        print(f"Could not load materials: {exc}", file=sys.stderr)
        return 2

    agent = StudyAgent(store, documents, model=args.model, effort=args.effort)

    print("Study coach ready.")
    print_status(store)
    if files:
        print(f"  Materials: {', '.join(f.name for f in files)}")
    else:
        print("  Materials: none (pass files or a folder to study from your own notes)")
    print()
    print(textwrap.indent(HELP, "  "))

    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in {"/quit", "/exit"}:
            break
        if line == "/help":
            print(HELP)
            continue
        if line == "/status":
            print_status(store)
            continue
        if line == "/exam" or line.startswith("/exam "):
            parts = line.split(maxsplit=2)
            try:
                exam = store.set_exam(parts[2] if len(parts) > 2 else "Exam", parts[1])
                print(f"  Saved: {exam['name']} on {exam['date']}")
            except (IndexError, ValueError):
                print("  Usage: /exam YYYY-MM-DD Exam name")
            continue

        message = line
        if line.startswith("/"):
            message = expand_shortcut(line)
            if message is None:
                print("  Unknown or incomplete command. Type /help.")
                continue

        print("\ncoach> ", end="", flush=True)
        try:
            stop = agent.send(
                message,
                on_text=lambda t: print(t, end="", flush=True),
                on_tool=lambda name: print(f"\n  [{name.replace('_', ' ')}]", flush=True),
            )
        except KeyboardInterrupt:
            print("\n[interrupted]")
            continue
        except anthropic.AuthenticationError:
            print(
                "\n[Authentication failed. Set ANTHROPIC_API_KEY or run `ant auth login`.]",
                file=sys.stderr,
            )
            return 1
        except anthropic.RateLimitError:
            print("\n[Rate limited. Wait a moment and try again.]")
            continue
        except anthropic.BadRequestError as exc:
            print(f"\n[Request rejected: {exc.message}]")
            continue
        except anthropic.APIStatusError as exc:
            print(f"\n[API error {exc.status_code}: {exc.message}]")
            continue
        except anthropic.APIConnectionError:
            print("\n[Could not reach the Claude API. Check your connection.]")
            continue
        if stop in STOP_NOTICES:
            print(f"\n{STOP_NOTICES[stop]}")
        print("\n")

    print("Good luck on your exam!")
    return 0
