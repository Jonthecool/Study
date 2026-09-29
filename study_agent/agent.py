"""The study coach: a Claude conversation with tools for quizzes and flashcards."""

from __future__ import annotations

import json
from collections.abc import Callable

import anthropic

from .store import StudyStore

DEFAULT_MODEL = "claude-opus-5-5"
# Models that accept server-side refusal fallbacks with `fallbacks: "default"`.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_STEPS = 25
MAX_BAD_JSON_RETRIES = 2

SYSTEM_PROMPT = """\
You are an exam study coach. Your job is to help the student actually learn the \
material and walk into the exam confident, not just to hand them answers.

How you teach:
- Favor active recall. Ask the student to retrieve an idea before you explain it, \
and have them explain concepts back in their own words.
- When you explain, start simple, build up, and use a concrete example or analogy. \
Check understanding with a quick follow-up question.
- When the student is wrong, say so clearly and kindly, explain the misconception, \
and come back to that idea later in the session.
- Keep replies focused and scannable; this is a terminal chat, so use short \
paragraphs and plain Markdown lists rather than large tables.

Study materials:
- If the student attached materials, ground your questions and explanations in them \
and mention which file an idea comes from. If you go beyond the materials, say so.
- If no materials are attached, ask what subject and exam they are preparing for \
and teach from your own knowledge.

Quizzes:
- Ask one question at a time and wait for the answer before revealing anything.
- Mix formats (multiple choice, short answer, explain-why, worked problems) and \
match the style of the real exam when the student tells you what it is like.
- After grading each answer, call record_quiz_answer so progress is tracked.

Flashcards:
- Create cards with add_flashcards when the student asks, or offer to turn missed \
quiz questions into cards. One fact or idea per card; keep the front a clear question.
- To review, fetch due cards with get_due_flashcards, show only the front, wait for \
the student's answer, then reveal the back, judge it, and call review_flashcard.

Planning and progress:
- At the start of a session, call get_study_status so you know today's date (New \
York time), the exam date and what is due.
- Use get_progress_report to find weak topics and steer practice toward them.
- For a study plan, work backwards from the exam date, front-load weak topics, and \
schedule spaced review rather than cramming.
"""

TOOLS = [
    {
        "name": "get_study_status",
        "description": "Get the current date and time (America/New_York), the exam name and "
        "date if set, days until the exam, and how many flashcards are due.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_exam",
        "description": "Save the exam the student is preparing for and its date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exam name, e.g. 'BIO 101 Midterm'."},
                "date": {"type": "string", "description": "Exam date as YYYY-MM-DD."},
            },
            "required": ["name", "date"],
        },
    },
    {
        "name": "add_flashcards",
        "description": "Save one or more flashcards to the student's deck for spaced repetition.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cards": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "front": {"type": "string", "description": "The prompt or question."},
                            "back": {"type": "string", "description": "The answer."},
                            "topic": {"type": "string", "description": "Short topic label."},
                        },
                        "required": ["front", "back", "topic"],
                    },
                },
            },
            "required": ["cards"],
        },
    },
    {
        "name": "get_due_flashcards",
        "description": "Get flashcards that are due for review, weakest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max cards to return (default 10)."},
                "topic": {"type": "string", "description": "Only return cards with this topic."},
            },
            "required": [],
        },
    },
    {
        "name": "review_flashcard",
        "description": "Record whether the student recalled a flashcard correctly. This "
        "schedules the card's next review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "card_id": {"type": "integer"},
                "correct": {"type": "boolean"},
            },
            "required": ["card_id", "correct"],
        },
    },
    {
        "name": "record_quiz_answer",
        "description": "Record the result of one graded quiz question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Short topic label."},
                "question": {"type": "string", "description": "The question that was asked."},
                "correct": {"type": "boolean"},
            },
            "required": ["topic", "question", "correct"],
        },
    },
    {
        "name": "get_progress_report",
        "description": "Get accuracy by topic, the weakest topics, and recent missed quiz "
        "questions.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]
# Replies are streamed, so let tool inputs stream too; inputs are validated below.
for _tool in TOOLS:
    _tool["eager_input_streaming"] = True
TOOL_SCHEMAS = {t["name"]: t["input_schema"] for t in TOOLS}

_JSON_TYPES = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate(value, schema: dict, path: str = "input") -> str | None:
    """Return an error message if ``value`` does not match ``schema``, else None."""
    expected = _JSON_TYPES[schema["type"]]
    # bool is a subclass of int in Python; don't accept True for an integer.
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        return f"{path} should be a {schema['type']}"
    if expected is dict:
        for key in schema.get("required", []):
            if key not in value:
                return f"{path} is missing required field '{key}'"
        for key, sub in schema.get("properties", {}).items():
            if key in value and (err := validate(value[key], sub, f"{path}.{key}")):
                return err
    if expected is list and "items" in schema:
        for i, item in enumerate(value):
            if err := validate(item, schema["items"], f"{path}[{i}]"):
                return err
    return None


class StudyAgent:
    def __init__(
        self,
        store: StudyStore,
        documents: list[dict] | None = None,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        effort: str = "medium",
    ):
        self.store = store
        self.documents = documents or []
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.messages: list[dict] = []

    # ----- tools ------------------------------------------------------------

    def run_tool(self, name: str, tool_input) -> str:
        if name not in TOOL_SCHEMAS:
            raise ValueError(f"Unknown tool '{name}'")
        if err := validate(tool_input, TOOL_SCHEMAS[name]):
            raise ValueError(f"Invalid input: {err}. Received: {json.dumps(tool_input)}")
        store = self.store
        if name == "get_study_status":
            result = store.status()
        elif name == "set_exam":
            result = store.set_exam(tool_input["name"], tool_input["date"])
        elif name == "add_flashcards":
            created = store.add_flashcards(tool_input["cards"])
            result = {"added": len(created), "ids": [c["id"] for c in created]}
        elif name == "get_due_flashcards":
            result = store.due_flashcards(
                limit=tool_input.get("limit", 10), topic=tool_input.get("topic")
            )
        elif name == "review_flashcard":
            card = store.review_flashcard(tool_input["card_id"], tool_input["correct"])
            result = {"id": card["id"], "box": card["box"], "next_due": card["due"]}
        elif name == "record_quiz_answer":
            store.record_quiz_answer(
                tool_input["topic"], tool_input["question"], tool_input["correct"]
            )
            result = {"recorded": True}
        else:  # get_progress_report
            result = store.progress_report()
        return json.dumps(result)

    def _tool_result(self, block) -> dict:
        try:
            content, is_error = self.run_tool(block.name, block.input), False
        except (ValueError, KeyError) as exc:
            content, is_error = f"Error: {exc}", True
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": content,
            "is_error": is_error,
        }

    # ----- conversation -----------------------------------------------------

    def _request_params(self) -> dict:
        params = {
            "model": self.model,
            "max_tokens": 64000,
            "system": SYSTEM_PROMPT,
            "tools": TOOLS,
            "messages": self.messages,
            "output_config": {"effort": self.effort},
            # Caches the materials and conversation so each turn is cheaper.
            "cache_control": {"type": "ephemeral"},
        }
        if self.model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _stream_once(self, on_text: Callable[[str], None]):
        for attempt in range(MAX_BAD_JSON_RETRIES + 1):
            try:
                with self.client.beta.messages.stream(**self._request_params()) as stream:
                    for text in stream.text_stream:
                        on_text(text)
                    return stream.get_final_message()
            except ValueError:
                # A tool input streamed as unparseable JSON; re-issue the request.
                if attempt == MAX_BAD_JSON_RETRIES:
                    raise

    def send(
        self,
        text: str,
        on_text: Callable[[str], None],
        on_tool: Callable[[str], None] | None = None,
    ) -> str:
        """Send a student message and run the tool loop until Claude replies.

        Returns the final stop reason. On an API error or refusal the turn is
        rolled back so the conversation can continue.
        """
        start = len(self.messages)
        content: list[dict] = []
        if start == 0:
            content.extend(self.documents)
        content.append({"type": "text", "text": text})
        self.messages.append({"role": "user", "content": content})

        try:
            for _ in range(MAX_TOOL_STEPS):
                message = self._stream_once(on_text)
                tool_uses = [b for b in message.content if b.type == "tool_use"]

                if message.stop_reason == "refusal" or (
                    message.stop_reason == "max_tokens" and tool_uses
                ):
                    del self.messages[start:]
                    return message.stop_reason

                self.messages.append({"role": "assistant", "content": message.content})
                if message.stop_reason != "tool_use" or not tool_uses:
                    return message.stop_reason

                results = []
                for block in tool_uses:
                    if on_tool:
                        on_tool(block.name)
                    results.append(self._tool_result(block))
                self.messages.append({"role": "user", "content": results})
            del self.messages[start:]
            return "too_many_tool_steps"
        except BaseException:
            del self.messages[start:]
            raise
