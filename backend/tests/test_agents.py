"""JSON extraction, validation and retry logic of app/agents.py. No network: a fake client stands in."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import agents
from app.agents import (AgentOutputError, DisruptionReply, EventModelReply, Question,
                        call_agent, extract_json, parse_reply)

GOOD_DISRUPTION = '{"disruption": {"node": "workshop_s2", "kind": "delayed", "params": {"delay_minutes": 120}}}'


# ---- extract_json -------------------------------------------------------------

def test_extract_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_from_markdown_fence_and_prose():
    text = 'Here you go:\n```json\n{"a": {"b": [1, 2]}}\n```\nHope this helps.'
    assert extract_json(text) == {"a": {"b": [1, 2]}}


def test_extract_skips_braces_that_are_not_json():
    text = 'Use {curly} notes, then {"ok": true}'
    assert extract_json(text) == {"ok": True}


def test_extract_handles_braces_inside_strings():
    assert extract_json('{"body": "see {room B}"}') == {"body": "see {room B}"}


def test_extract_raises_when_no_object():
    with pytest.raises(ValueError):
        extract_json("Sorry, I could not read the file.")


# ---- parse_reply ----------------------------------------------------------------

def test_parse_valid_disruption():
    out = parse_reply(GOOD_DISRUPTION, DisruptionReply)
    assert out.disruption.node == "workshop_s2"
    assert out.disruption.params["delay_minutes"] == 120


def test_parse_event_model_uses_from_to_aliases():
    text = ('{"event_model": {"event_id": "x", "nodes": [{"id": "a", "type": "resource", "label": "A"}],'
            ' "edges": [{"from": "a", "to": "a", "type": "located_at"}]}}')
    model = parse_reply(text, EventModelReply).event_model
    assert model.edges[0].from_id == "a"


def test_parse_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        parse_reply('{"disruption": {"node": "x", "kind": "exploded"}}', DisruptionReply)


def test_parse_rejects_wrong_top_level_key():
    with pytest.raises(ValidationError):
        parse_reply('{"event": {"node": "x", "kind": "lost"}}', DisruptionReply)


def test_parse_returns_questions():
    text = '{"questions": [{"prompt": "Which hall?", "options": ["Main", "B"]}]}'
    out = parse_reply(text, DisruptionReply)
    assert out == [Question(prompt="Which hall?", options=["Main", "B"])]


# ---- call_agent with a fake client (retry + questions) -------------------------------

class Result:  # named like the SDK class, because describe_event uses the class name
    def __init__(self, total):
        self.status = "success"
        self.correlation_id = 1
        self.data = SimpleNamespace(cost=SimpleNamespace(total=Decimal(total)), description=None,
                                    message=None, filename=None)


class FakeRun:
    def __init__(self, content, questions=None, cost="0.01"):
        self.thread = SimpleNamespace(id="thread-1")
        self._events = [Result(cost)]
        self._reply = SimpleNamespace(content=content, questions=questions or [], files=[])

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()

    async def result(self):
        return self._reply


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    async def start(self, **kw):
        self.sent.append(("start", kw["message"]))
        return self.replies.pop(0)

    async def send(self, thread_id, **kw):
        self.sent.append(("send", kw["message"]))
        return self.replies.pop(0)


def fake_client(*replies):
    return SimpleNamespace(chat=FakeChat(replies))


@pytest.fixture(autouse=True)
def agent_ids(monkeypatch):
    for env in agents.AGENT_ENV.values():
        monkeypatch.setenv(env, "test-agent")


def run(coro):
    return asyncio.run(coro)


def test_call_agent_valid_first_time():
    client = fake_client(FakeRun(GOOD_DISRUPTION))
    events = []
    res = run(call_agent(client, "impact", "hi", DisruptionReply, on_event=events.append))
    assert res.data.disruption.kind.value == "delayed"
    assert res.cost == Decimal("0.01")
    assert [e["event"] for e in events] == ["Result"]
    assert len(client.chat.sent) == 1


def test_call_agent_retries_once_with_error_then_succeeds():
    client = fake_client(FakeRun("not json at all"), FakeRun(GOOD_DISRUPTION))
    res = run(call_agent(client, "impact", "hi", DisruptionReply))
    assert res.data.disruption.node == "workshop_s2"
    assert client.chat.sent[1][0] == "send"                      # same thread
    assert "no JSON object found" in client.chat.sent[1][1]      # error is passed back
    assert res.cost == Decimal("0.02")                           # both attempts counted


def test_call_agent_raises_after_second_invalid_reply():
    client = fake_client(FakeRun("nope"), FakeRun('{"disruption": {"node": "x"}}'))
    with pytest.raises(AgentOutputError):
        run(call_agent(client, "impact", "hi", DisruptionReply))


def test_call_agent_returns_builtin_questions():
    q = SimpleNamespace(prompt="Which speaker?", options=["Speaker 1", "Speaker 2"])
    client = fake_client(FakeRun("", questions=[q]))
    res = run(call_agent(client, "impact", "hi", DisruptionReply))
    assert res.data is None
    assert res.questions[0].options == ["Speaker 1", "Speaker 2"]


def test_call_agent_missing_agent_id(monkeypatch):
    monkeypatch.delenv("COMINTY_AGENT_IMPACT")
    with pytest.raises(RuntimeError, match="COMINTY_AGENT_IMPACT"):
        run(call_agent(fake_client(), "impact", "hi", DisruptionReply))
