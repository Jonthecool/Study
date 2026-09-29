import json
from types import SimpleNamespace

import pytest

from study_agent.agent import TOOL_SCHEMAS, StudyAgent, validate
from study_agent.store import StudyStore


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


class FakeStream:
    def __init__(self, message):
        self.message = message
        self.text_stream = [b.text for b in message.content if b.type == "text"]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    """Replays canned responses and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **params):
        self.requests.append({**params, "messages": list(params["messages"])})
        return FakeStream(self.responses.pop(0))


def reply(stop_reason, *blocks):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


@pytest.fixture
def store(tmp_path):
    return StudyStore(tmp_path)


def test_validate():
    schema = TOOL_SCHEMAS["add_flashcards"]
    assert validate({"cards": [{"front": "f", "back": "b", "topic": "t"}]}, schema) is None
    assert "missing required field 'topic'" in validate(
        {"cards": [{"front": "f", "back": "b"}]}, schema
    )
    assert "should be a integer" in validate(
        {"card_id": True, "correct": True}, TOOL_SCHEMAS["review_flashcard"]
    )


def test_tool_loop_runs_tools_and_sends_materials_once(store):
    docs = [{"type": "document", "source": {"type": "text", "media_type": "text/plain",
                                            "data": "notes"}, "title": "notes.md"}]
    client = FakeClient(
        [
            reply(
                "tool_use",
                text("Saving cards."),
                tool_use("t1", "add_flashcards",
                         {"cards": [{"front": "What is ATP?", "back": "Energy", "topic": "Cells"}]}),
            ),
            reply("end_turn", text("Done!")),
            reply("end_turn", text("Hi again")),
        ]
    )
    agent = StudyAgent(store, docs, client=client)
    out, tools = [], []

    assert agent.send("make cards", out.append, tools.append) == "end_turn"
    assert "".join(out) == "Saving cards.Done!"
    assert tools == ["add_flashcards"]
    assert len(store.data["flashcards"]) == 1

    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "t1" and result["is_error"] is False
    assert json.loads(result["content"]) == {"added": 1, "ids": [1]}

    agent.send("hello", out.append)
    first_user, last_user = client.requests[2]["messages"][0], client.requests[2]["messages"][-1]
    assert first_user["content"][0]["type"] == "document"
    assert last_user["content"] == [{"type": "text", "text": "hello"}]
    assert client.requests[0]["fallbacks"] == "default"
    assert client.requests[0]["output_config"] == {"effort": "medium"}


def test_invalid_tool_input_returns_error_result(store):
    client = FakeClient(
        [
            reply("tool_use", tool_use("t1", "review_flashcard", {"card_id": 1})),
            reply("end_turn", text("ok")),
        ]
    )
    StudyAgent(store, client=client).send("review", lambda t: None)
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert "missing required field 'correct'" in result["content"]


def test_refusal_rolls_back_turn(store):
    client = FakeClient([reply("refusal")])
    agent = StudyAgent(store, client=client)
    assert agent.send("something", lambda t: None) == "refusal"
    assert agent.messages == []


def test_api_error_rolls_back_turn(store):
    class Boom(Exception):
        pass

    client = FakeClient([])
    client.beta.messages.stream = lambda **kw: (_ for _ in ()).throw(Boom())
    agent = StudyAgent(store, client=client)
    with pytest.raises(Boom):
        agent.send("hi", lambda t: None)
    assert agent.messages == []
