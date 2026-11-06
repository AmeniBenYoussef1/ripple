"""Calls to Ripple's 4 Cominty agents.

Each agent replies with ONE JSON object (see skills/ripple-contracts). This module:
  1. sends the message (+ files) to the agent id from .env
  2. forwards every streamed event to a callback (for the live agent panel)
  3. adds up the cost from the Result events
  4. extracts the JSON, validates it with pydantic, and re-asks once if it is invalid
  5. returns clarifying questions instead of data when the agent asks them

The pure parsing functions (extract_json, parse_reply) have no network and are unit tested.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Awaitable, Callable, Optional, Type, TypeVar, Union

from pydantic import BaseModel, Field, ValidationError

from .models import Action, Candidate, Disruption, EventModel, OutboxMessage, Scenario

# ---- Reply shapes (top-level keys from reference.md) ------------------------


class EventModelReply(BaseModel):
    event_model: EventModel


class DisruptionReply(BaseModel):
    disruption: Disruption


class CandidatesReply(BaseModel):
    candidates: list[Candidate] = Field(min_length=1)


class ScenariosReply(BaseModel):
    scenarios: list[Scenario] = Field(min_length=1)


class CoordinatorReply(BaseModel):
    messages: list[OutboxMessage]
    revised_schedule: list[dict[str, Any]] = Field(default_factory=list)
    volunteer_tasks: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)


class Question(BaseModel):
    prompt: str
    options: list[str] = Field(default_factory=list)


R = TypeVar("R", bound=BaseModel)

# ---- Pure parsing (unit tested) ---------------------------------------------


class AgentOutputError(Exception):
    """The agent's reply could not be turned into the expected JSON, even after one retry."""


def extract_json(text: str) -> dict[str, Any]:
    """Return the first JSON object found in `text`.

    Agents are told to reply with JSON only, but may still wrap it in ```json fences
    or add a sentence around it. We scan for the first '{' that starts a valid object.
    """
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("no JSON object found in the reply")


def parse_reply(text: str, shape: Type[R]) -> Union[R, list[Question]]:
    """Turn reply text into `shape`, or into a list of questions if the agent asked some.

    Raises ValueError (no JSON) or pydantic.ValidationError (wrong shape).
    """
    obj = extract_json(text)
    if "questions" in obj and not any(k in obj for k in shape.model_fields):
        return [Question.model_validate(q) for q in obj["questions"]]
    return shape.model_validate(obj)


def short_error(err: Exception, limit: int = 1500) -> str:
    """A compact error text to send back to the agent on the retry."""
    msg = str(err)
    return msg if len(msg) <= limit else msg[:limit] + " ..."


# ---- Live calls ---------------------------------------------------------------

EventCallback = Callable[[dict[str, Any]], Union[None, Awaitable[None]]]


@dataclass
class AgentResult:
    agent: str                                  # e.g. "impact"
    data: Optional[BaseModel] = None            # the validated reply, or None if questions
    questions: list[Question] = field(default_factory=list)
    cost: Decimal = Decimal(0)                  # sum of Result.data.cost.total (all attempts)
    thread_id: Optional[str] = None
    files: list[Any] = field(default_factory=list)   # ConversationFile objects from the reply
    raw: str = ""                               # last reply text, for debugging
    events: list[dict[str, Any]] = field(default_factory=list)  # streamed steps, kept for DEMO_MODE replay


AGENT_ENV = {
    "impact": "COMINTY_AGENT_IMPACT",
    "finder": "COMINTY_AGENT_FINDER",
    "replanner": "COMINTY_AGENT_REPLANNER",
    "coordinator": "COMINTY_AGENT_COORDINATOR",
}


def agent_id(agent: str) -> str:
    value = os.environ.get(AGENT_ENV[agent])
    if not value:
        raise RuntimeError(f"{AGENT_ENV[agent]} is not set in backend/.env")
    return value


def describe_event(ev: Any) -> dict[str, Any]:
    """Flatten one SDK stream event into a small dict for the UI."""
    data = getattr(ev, "data", None)
    description = ""
    if data is not None:
        description = (getattr(data, "description", None) or getattr(data, "message", None)
                       or getattr(data, "filename", None) or "")
        if type(ev).__name__ == "ToolCall":
            description = f"{data.name}: {data.description}"
    return {"event": type(ev).__name__, "status": str(getattr(ev, "status", "")),
            "description": description, "id": getattr(ev, "correlation_id", None)}


async def _consume(run: Any, agent: str, on_event: Optional[EventCallback],
                   log: Optional[list] = None) -> tuple[Any, Decimal]:
    """Stream the run's events to the callback (and `log`); return (final message, cost)."""
    cost = Decimal(0)
    async for ev in run:
        if type(ev).__name__ == "Result":
            cost += ev.data.cost.total
        item = {"agent": agent, **describe_event(ev)}
        if log is not None:
            log.append(item)
        if on_event is not None:
            out = on_event(item)
            if hasattr(out, "__await__"):
                await out
    return await run.result(), cost


async def call_agent(
    client: Any,
    agent: str,
    message: str,
    shape: Type[R],
    *,
    file_ids: Optional[list[str]] = None,
    on_event: Optional[EventCallback] = None,
    thread_id: Optional[str] = None,
    thread_name: Optional[str] = None,
    max_steps: int = 8,
    check: Optional[Callable[[Any], None]] = None,
) -> AgentResult:
    """Send `message` to one agent and return its validated reply (or its questions).

    `check(parsed)` is an optional extra rule (e.g. "the node id exists in the model");
    it raises ValueError to trigger the same single retry as invalid JSON.
    """
    aid = agent_id(agent)
    if thread_id is None:
        run = await client.chat.start(agent_id=aid, message=message, name=thread_name,
                                      file_ids=file_ids, max_steps=max_steps)
        thread_id = str(run.thread.id)
    else:
        run = await client.chat.send(thread_id, agent_id=aid, message=message,
                                     file_ids=file_ids, max_steps=max_steps)
    log: list[dict[str, Any]] = []
    reply, cost = await _consume(run, agent, on_event, log)
    result = AgentResult(agent=agent, cost=cost, thread_id=thread_id,
                         files=list(reply.files), raw=reply.content, events=log)

    # Built-in clarifying questions (reply.questions) win over everything else.
    if reply.questions:
        result.questions = [Question(prompt=q.prompt, options=list(q.options)) for q in reply.questions]
        return result

    def parse_and_check(text: str):
        parsed = parse_reply(text, shape)
        if check is not None and not isinstance(parsed, list):
            check(parsed)
        return parsed

    try:
        parsed = parse_and_check(reply.content)
    except (ValueError, ValidationError) as err:
        # One retry in the same thread, telling the agent exactly what was wrong.
        retry_msg = (
            "Your last reply could not be used. Reply again with ONE JSON object only, "
            f"with the top-level key(s) {list(shape.model_fields)}. Error:\n{short_error(err)}"
        )
        run = await client.chat.send(thread_id, agent_id=aid, message=retry_msg, max_steps=max_steps)
        reply, more = await _consume(run, agent, on_event, log)
        result.cost += more
        result.raw = reply.content
        result.files = list(reply.files) or result.files
        try:
            parsed = parse_and_check(reply.content)
        except (ValueError, ValidationError) as err2:
            raise AgentOutputError(
                f"{agent} agent returned invalid JSON twice: {short_error(err2, 400)}") from err2

    if isinstance(parsed, list):
        result.questions = parsed
    else:
        result.data = parsed
    return result


# ---- One function per agent ------------------------------------------------------


async def build_event_model(client, message: str, file_ids: list[str], **kw) -> AgentResult:
    """Impact Analyst, setup: files -> {"event_model": ...}."""
    return await call_agent(client, "impact", message, EventModelReply, file_ids=file_ids, **kw)


async def read_disruption(client, message: str, **kw) -> AgentResult:
    """Impact Analyst, on an organizer message: -> {"disruption": ...} or questions."""
    return await call_agent(client, "impact", message, DisruptionReply, **kw)


async def find_resources(client, message: str, **kw) -> AgentResult:
    """Resource Finder: lost/insufficient resource -> {"candidates": [...]}."""
    return await call_agent(client, "finder", message, CandidatesReply, **kw)


async def replan(client, message: str, **kw) -> AgentResult:
    """Replanner: affected items (+ candidates) -> {"scenarios": [...]}."""
    return await call_agent(client, "replanner", message, ScenariosReply, **kw)


async def coordinate(client, message: str, **kw) -> AgentResult:
    """Coordinator: chosen scenario + groups -> {"messages": [...], ...} and files."""
    return await call_agent(client, "coordinator", message, CoordinatorReply, **kw)
