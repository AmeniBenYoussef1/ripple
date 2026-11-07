"""Ripple orchestrator: agents read and propose, code decides what runs and what wins.

    handle_disruption(event, text):
        Impact Analyst -> Disruption (or questions)
        propagate() -> waves                     (code)
        route()     -> run / skipped / cached    (code)
        Resource Finder -> candidates            (only if woken)
        Replanner       -> scenarios             (only if woken, gets the candidates)
        score()     -> ranked scenarios          (code)
        Coordinator: waits for the organizer's choice, or runs at once when there is
                     nothing to choose (the Replanner was skipped)

    choose_scenario(event, scenario_id):
        Coordinator -> message drafts; code builds the .ics and .csv; the model is updated

Every live agent reply is recorded to fixtures/<disruption>/<agent>.json.
With DEMO_MODE=true those recordings are replayed instead of calling Cominty.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel

from .agents import (AgentResult, CandidatesReply, CoordinatorReply, DisruptionReply,
                     EventModelReply, EventCallback, Question, ScenariosReply, call_agent)
from .exports import NEWCOMERS_GROUP, build_attendee_csv, build_ics
from .impact_engine import propagate, route
from .models import EventModel, NodeType, Scenario
from .recipients import address, member_groups, one_message_per_person, resolve
from .rooms import settle_rooms
from .whatif import attendee_group
from .scoring import score

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND / "fixtures"
EVENTS = BACKEND / "events"
DATA = BACKEND / "demo_data"
GENERATED_MODEL = DATA / "event_model_generated.json"
LATE_REGISTRATIONS = DATA / "late_registrations.csv"   # the newcomers of a "resized" disruption

# engine agent name -> (agents.py key, reply shape, max tool rounds)
SPEC: dict[str, tuple[str, type[BaseModel], int]] = {
    "impact_analyst": ("impact", DisruptionReply, 6),
    "setup_analyst": ("impact", EventModelReply, 14),   # the Impact Analyst at setup: files -> event model
    "resource_finder": ("finder", CandidatesReply, 10),
    "replanner": ("replanner", ScenariosReply, 6),
    "coordinator": ("coordinator", CoordinatorReply, 8),
}


class OrchestratorError(Exception):
    """Something the UI should show as an error message."""


def demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "false").strip().lower() in ("1", "true", "yes")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "disruption"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- Event store: one JSON file per event (+ a folder for its files) ---------------


@dataclass
class Event:
    id: str
    path: Path
    data: dict[str, Any]

    @property
    def model(self) -> EventModel:
        return EventModel.model_validate(self.data["model"])

    @property
    def files_dir(self) -> Path:
        """Files Ripple builds for this event (.ics, .csv)."""
        return self.path.parent / self.id / "files"

    @property
    def inputs_dir(self) -> Path:
        """The organizer's uploaded files, kept so code can read recipients and participants from them."""
        return self.path.parent / self.id / "inputs"

    @property
    def data_dir(self) -> Path:
        """Where this event's files are: its uploads, or the CodeSprint demo files."""
        return self.inputs_dir if self.data.get("inputs") else DATA

    def set_model(self, model: EventModel) -> None:
        self.data["model"] = model.model_dump(mode="json", by_alias=True)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, default=str) + "\n", encoding="utf-8")


def new_event(event_id: str, model_path: Optional[Path] = GENERATED_MODEL, events_dir: Path = EVENTS,
              search_area: str = "", title: str = "") -> Event:
    """Start (or reset) an event from a saved model file, or blank (model_path=None) until it is analyzed."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", event_id):
        raise OrchestratorError("event id: use 1-64 letters, digits, '-' or '_'")
    if model_path is None:
        model = EventModel(event_id=event_id, title=title or event_id)
    else:
        if not model_path.exists():
            raise OrchestratorError(f"no saved event model at {model_path}; run scripts/try_setup.py first")
        model = EventModel.model_validate(json.loads(model_path.read_text(encoding="utf-8")))
    ev = Event(event_id, events_dir / f"{event_id}.json",
               {"event_id": event_id, "title": title or model.title, "search_area": search_area, "model": None,
                "history": [], "cache": {}, "costs": [], "setup": {"status": "not_analyzed"},
                "plans": [], "log": []})
    shutil.rmtree(ev.files_dir.parent, ignore_errors=True)
    ev.set_model(model)
    add_plan_version(ev, "Original plan, from your event files", None)
    log(ev, "setup", "Event created from the saved event model" if model_path else "Event created, waiting for its files")
    ev.save()
    return ev


def reset_problems(event: Event) -> Event:
    """Start the day over but keep the analysis: same files, same model, no problems (cheap retakes)."""
    snap = event.data.get("analyzed_model")
    if not snap:
        raise OrchestratorError("this event has not been analyzed yet")
    setup_cost = [c for c in event.data.get("costs", []) if c["disruption"] == "setup"]
    event.data.update(model=snap, history=[], cache={}, costs=setup_cost, plans=[], log=[])
    event.data["setup"]["confirmed"] = False
    shutil.rmtree(event.files_dir, ignore_errors=True)
    add_plan_version(event, "Original plan, from your event files", None)
    log(event, "setup", "Event started over (same files and analysis)")
    event.save()
    return event


def list_events(events_dir: Path = EVENTS) -> list[dict[str, Any]]:
    out = []
    for p in sorted(events_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        out.append({"id": d.get("event_id", p.stem), "title": d.get("title") or (d.get("model") or {}).get("title") or p.stem,
                    "problems": len(d.get("history", [])), "setup": (d.get("setup") or {}).get("status", "not_analyzed")})
    return out


# ---- Event log and plan versions (shown on the History page) ----------------------------

def log(event: Event, kind: str, text: str, **data: Any) -> None:
    """One line of the event's timeline: setup, problem, decision, plan, constraint, edit, approval, action."""
    event.data.setdefault("log", []).append({"at": now_iso(), "kind": kind, "text": text, **data})


def add_plan_version(event: Event, title: str, disruption_id: Optional[str]) -> dict[str, Any]:
    """The event's original plan is version 0; each problem's plans count from v1."""
    plans = event.data.setdefault("plans", [])
    version = 0 if disruption_id is None else 1 + sum(1 for p in plans if p.get("disruption") == disruption_id)
    plan = {"version": version, "at": now_iso(), "title": title, "disruption": disruption_id}
    plans.append(plan)
    if disruption_id is not None:
        log(event, "plan", f"Plan v{version}: {title}", disruption=disruption_id, version=version)
    return plan


def load_event(event_id: str, events_dir: Path = EVENTS) -> Event:
    path = events_dir / f"{event_id}.json"
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", event_id) or not path.exists():
        raise OrchestratorError(f"unknown event {event_id!r}")
    return Event(event_id, path, json.loads(path.read_text(encoding="utf-8")))


# ---- Runner: live calls with recording, or replay (DEMO_MODE) --------------------

StageCallback = Callable[[str, dict[str, Any]], Any]


@dataclass
class Runner:
    client: Any = None                         # AsyncCominty, not needed in demo mode
    demo: bool = field(default_factory=demo_mode)
    fixtures_dir: Path = FIXTURES
    on_event: Optional[EventCallback] = None   # agent progress (ToolCall, LlmStep, ...)
    on_stage: Optional[StageCallback] = None   # flow progress (impact, routing, scenarios, outbox)
    prefer_recordings: bool = False            # live mode: replay what is recorded, call only what is missing
    replay_delay: tuple[float, float] = (0.0, 0.0)  # DEMO_MODE pacing between replayed steps, in seconds
    replay_max_per_agent: float = 6.0          # cap so a long recording still replays quickly

    def _fixture(self, slug: str, agent: str) -> Path:
        return self.fixtures_dir / slug / f"{agent}.json"

    async def stage(self, name: str, payload: dict[str, Any]) -> None:
        if self.on_stage is not None:
            out = self.on_stage(name, payload)
            if hasattr(out, "__await__"):
                await out

    async def call(self, slug: str, agent: str, message: str, *, thread_name: str,
                   file_ids: Optional[list[str]] = None, check=None, name: Optional[str] = None,
                   thread_id: Optional[str] = None, extra: Optional[dict] = None) -> AgentResult:
        try:
            return await self._call(slug, agent, message, thread_name=thread_name, file_ids=file_ids, check=check,
                                    name=name, thread_id=thread_id, extra=extra)
        except Exception as err:   # tell the UI which agent failed, in plain words, then let the caller handle it
            await self.stage("agent_failed", {"agent": "impact_analyst" if agent == "setup_analyst" else agent,
                                              "reason": failure_reason(err), "detail": f"{type(err).__name__}: {err}"})
            raise

    async def _call(self, slug: str, agent: str, message: str, *, thread_name: str,
                    file_ids: Optional[list[str]] = None, check=None, name: Optional[str] = None,
                    thread_id: Optional[str] = None, extra: Optional[dict] = None) -> AgentResult:
        key, shape, max_steps = SPEC[agent]
        path = self._fixture(slug, name or agent)   # name: e.g. "coordinator-B", one recording per option
        if self.demo or (self.prefer_recordings and path.exists()):
            if not path.exists():
                raise OrchestratorError(f"DEMO_MODE: no recording at {path.relative_to(self.fixtures_dir.parent)}")
            return await self._replay(path, key, shape)
        if self.client is None:
            raise OrchestratorError("no Cominty client (set DEMO_MODE=true to replay recordings)")
        res = await call_agent(self.client, key, message, shape, file_ids=file_ids, on_event=self.on_event,
                               thread_name=thread_name, max_steps=max_steps, check=check, thread_id=thread_id)
        self._record(path, agent, message, res, extra)
        return res

    async def _replay(self, path: Path, key: str, shape: type[BaseModel]) -> AgentResult:
        rec = json.loads(path.read_text(encoding="utf-8"))
        res = AgentResult(agent=key, cost=Decimal(rec["cost"]), thread_id=rec.get("thread_id"),
                          raw=rec.get("raw", ""),
                          questions=[Question.model_validate(q) for q in rec.get("questions", [])])
        if rec.get("data") is not None:
            res.data = shape.model_validate(rec["data"])
        # Replay the recorded steps (same descriptions as live), paced so the UI can animate them.
        steps = [e for e in rec.get("events", []) if e.get("event") != "Result"]
        running = sum(1 for e in steps if e.get("status") == "running") or 1
        lo, hi = self.replay_delay
        cap = self.replay_max_per_agent / running
        for e in steps:
            if e.get("status") == "running" and hi > 0:
                await asyncio.sleep(min(random.uniform(lo, hi), cap))
            await self._emit({**e, "agent": key, "replay": True})
        await self._emit({"agent": key, "event": "Replay", "status": "success", "replay": True,
                          "description": f"replayed recording {path.parent.name}/{path.name}", "id": None})
        return res

    async def _emit(self, item: dict[str, Any]) -> None:
        if self.on_event is not None:
            out = self.on_event(item)
            if hasattr(out, "__await__"):
                await out

    def _record(self, path: Path, agent: str, message: str, res: AgentResult, extra: Optional[dict] = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"agent": agent, "recorded_at": now_iso(), "thread_id": res.thread_id, "cost": str(res.cost),
               "message": message, "raw": res.raw,
               "data": res.data.model_dump(mode="json", by_alias=True) if res.data else None,
               "questions": [q.model_dump() for q in res.questions], "events": res.events,
               "agent_files": [{"name": f.name, "has_url": bool(getattr(f, "url", ""))} for f in res.files],
               **(extra or {})}
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    async def upload(self, paths: list[Path]) -> list[str]:
        if self.demo or self.client is None:
            return []
        return [(await self.client.chat.upload_file(p)).id for p in paths]


def failure_reason(err: Exception) -> str:
    """Why an agent failed, for the organizer (the technical text goes to Behind the scenes)."""
    name = type(err).__name__
    text = str(err)
    if "no recording" in text:
        return "This step wasn't recorded, so it works in Live mode only."
    if name == "AgentOutputError":
        return "Its answer couldn't be read, even after asking again."
    if name == "RateLimitError":
        return "The AI platform is busy. Try again in a minute."
    if name in ("AuthError", "PermissionError"):
        return "Ripple couldn't sign in to the AI platform."
    if name in ("APIConnectionError", "StreamInterrupted", "ServerError"):
        return "The connection to the AI platform was lost. Try again."
    return "Something went wrong while it worked. Try again."


# ---- Small model helpers (no hard-coded ids) ---------------------------------------


def remaining_budget(model: EventModel) -> Optional[float]:
    for n in model.nodes:
        if n.type == NodeType.constraint and isinstance(n.attrs.get("remaining_eur"), (int, float)):
            return float(n.attrs["remaining_eur"])
    return None


def group_sizes(model: EventModel) -> list[dict[str, Any]]:
    return [{"id": n.id, "label": n.label, "size": n.attrs.get("size")}
            for n in model.nodes if n.type == NodeType.group]


def needed_capacity(model: EventModel, resource_id: str, new_size: Optional[int]) -> Optional[int]:
    """Largest group sized_by this resource (or the new size after a resize)."""
    sizes = {n.id: n.attrs.get("size") for n in model.nodes if n.type == NodeType.group}
    vals = [sizes[e.from_id] for e in model.edges
            if e.to_id == resource_id and e.type.value == "sized_by" and isinstance(sizes.get(e.from_id), int)]
    if new_size:
        vals.append(int(new_size))
    return max(vals) if vals else None


def event_date(model: EventModel, data_dir: Optional[Path] = None) -> Optional[str]:
    """The event's date (YYYY-MM-DD): a node that carries it, else the "date" of the event's schedule.json."""
    for n in model.nodes:
        if n.attrs.get("date"):
            return str(n.attrs["date"])
    schedule = (data_dir / "schedule.json") if data_dir else None
    if schedule and schedule.exists():
        try:
            date = json.loads(schedule.read_text(encoding="utf-8")).get("date")
        except (ValueError, OSError):
            return None
        if isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            return date
    return None


# Where each kind of problem can start. A delay starts at a schedule item or commitment
# (a late speaker delays their session), never at a group: groups only receive news.
KIND_STARTS_AT: dict[str, set[NodeType]] = {
    "lost": {NodeType.resource},
    "supplier_failed": {NodeType.commitment, NodeType.resource},
    "delayed": {NodeType.schedule_item, NodeType.commitment},
    "resized": {NodeType.group},
    "cost_changed": {NodeType.constraint},
}


def node_checker(model: EventModel):
    types = {n.id: n.type for n in model.nodes}

    def check(parsed: DisruptionReply) -> None:
        d = parsed.disruption
        if d.node not in types:
            raise ValueError(f"node {d.node!r} is not in the event model; use one of: {sorted(types)}")
        allowed = KIND_STARTS_AT[d.kind.value]
        if types[d.node] not in allowed:
            raise ValueError(
                f"a {d.kind.value!r} disruption must start at a node of type "
                f"{sorted(t.value for t in allowed)}, but {d.node!r} is a {types[d.node].value}. "
                "Pick the node where the problem starts (e.g. a late person delays the session they lead).")
    return check


def avg_cost(event: Event, agent: str) -> Optional[Decimal]:
    vals = [Decimal(c["cost"]) for c in event.data["costs"] if c["agent"] == agent]
    return sum(vals) / len(vals) if vals else None


def avg_recorded_cost(fixtures_dir: Path, agent: str) -> Optional[Decimal]:
    """Average real cost of this agent over the recorded runs in fixtures/."""
    vals = []
    for f in fixtures_dir.glob(f"*/{agent}.json"):
        try:
            vals.append(Decimal(json.loads(f.read_text(encoding="utf-8"))["cost"]))
        except (KeyError, ValueError, OSError):
            continue
    return sum(vals) / len(vals) if vals else None


def saved_estimate(event: Event, agent: str, fixtures_dir: Path) -> dict[str, Any]:
    """What a skipped or cached agent would have cost: its average on this event, else in the recordings."""
    here = avg_cost(event, agent)
    if here is not None:
        return {"cost": str(here), "estimate": False, "basis": "average on this event"}
    rec = avg_recorded_cost(fixtures_dir, agent)
    if rec is not None:
        return {"cost": str(rec), "estimate": True, "basis": "average of recorded runs"}
    return {"cost": None, "estimate": True, "basis": "no data yet"}


def add_cost(event: Event, disruption_id: str, agent: str, cost: Decimal) -> None:
    event.data["costs"].append({"disruption": disruption_id, "agent": agent, "cost": str(cost)})


def total_cost(event: Event) -> Decimal:
    return sum((Decimal(c["cost"]) for c in event.data["costs"]), Decimal(0))


def apply_scenario(model: EventModel, origin: str, kind: str, s: Scenario,
                   venue_name: Optional[str] = None) -> EventModel:
    """Write the chosen scenario back into the model, so the next disruption sees it."""
    nodes = {n.id: n for n in model.nodes}
    for ch in s.changes:
        n = nodes.get(ch.get("node"))
        if n is None:
            continue
        if isinstance(ch.get("set"), dict):
            n.attrs.update(ch["set"])
        if ch.get("replace_with"):
            n.attrs["replaced_by"] = venue_name or ch["replace_with"]
    if kind == "lost" and origin in nodes:
        node = nodes[origin]
        if s.capacity is not None:
            node.attrs["capacity"] = s.capacity          # the replacement venue's capacity
        if node.attrs.get("replaced_by"):                # from now on it is the new venue
            node.attrs.setdefault("original_label", node.label)
            node.label = str(node.attrs["replaced_by"])
    return model


# ---- Messages to agents (JSON in, JSON out) ----------------------------------------


def impact_message(model: EventModel, text: str) -> str:
    compact = {"nodes": [{"id": n.id, "type": n.type.value, "label": n.label, "attrs": n.attrs} for n in model.nodes],
               "edges": [{"from": e.from_id, "to": e.to_id, "type": e.type.value} for e in model.edges]}
    return (
        f'Organizer message: "{text}"\n\n'
        'Reply with {"disruption": {...}} as in the ripple-contracts skill.\n'
        '- "node" must be one id from the event model below: the node where the problem starts.\n'
        "- lost: a resource is gone. supplier_failed: a supplier fails a commitment. "
        "delayed: a schedule item or commitment moves in time, params.delay_minutes (a late person "
        "delays the session they lead: point to that session, not to the person). resized: a group "
        "changes size, params.new_size = the new TOTAL size (the current size is attrs.size). "
        "cost_changed: params.new_value.\n"
        "- If you cannot tell which node is meant, ask questions instead.\n\n"
        f"Event model:\n{dumps(compact)}"
    )


def finder_message(model: EventModel, finder_nodes: list[str], effects: dict, detail: str,
                   new_size: Optional[int], area: str, data_dir: Optional[Path] = None) -> str:
    nodes = {n.id: n for n in model.nodes}
    needs = [{"resource": nodes[r].model_dump(mode="json"), "problem": effects[r],
              "requirements": {"min_capacity": needed_capacity(model, r, new_size), "date": event_date(model, data_dir),
                               "area": area, "max_extra_cost_eur": remaining_budget(model)}}
             for r in finder_nodes]
    return (
        'Find replacements. Reply with {"candidates": [...]} (3 to 5) as in the ripple-contracts skill. '
        'Search the web; every candidate needs its source url; availability is always "to confirm". '
        "Prefer places that fit min_capacity and are in or near the area. "
        'Add "address" (the street address exactly as the source page gives it) and "lat"/"lon" '
        "only if the page states them; otherwise null. Never guess an address.\n"
        f"Problem: {detail}\n{dumps(needs)}"
    )


def replanner_message(model: EventModel, slice_ids: list[str], effects: dict, disruption: dict,
                      candidates: list[dict]) -> str:
    nodes = {n.id: n for n in model.nodes}
    payload = {
        "disruption": disruption,
        "affected": [{**nodes[i].model_dump(mode="json"), "effect": effects.get(i, "context")} for i in slice_ids],
        "groups": group_sizes(model),
        "candidates": candidates,
        "remaining_budget_eur": remaining_budget(model),
    }
    return (
        'Propose 2 or 3 scenarios. Reply with {"scenarios": [...]} as in the ripple-contracts skill.\n'
        '- ids "A", "B", "C". "changes" reference node ids from "affected" ({"node", "set"} or {"node", "replace_with"}).\n'
        '- burdens: one entry per affected group, "group" = the group id, "people" = its size, '
        '"minutes" = your estimate of time lost per person.\n'
        '- "capacity" = capacity of the venue used in that scenario (null if unknown), "attendees" = the attendees group size.\n'
        '- "extra_cost_eur": null when unknown. List every unverified fact in "to_confirm". Do not rank.\n'
        '- "accessibility_ok" and "within_opening_hours" mean "not known to fail": set them to false ONLY when a '
        'source says the scenario fails that requirement. If unknown, keep true and add it to "to_confirm".\n'
        f"{dumps(payload)}"
    )


def newcomers_count(disruption: dict, data_dir: Path = DATA) -> int:
    """People in late_registrations.csv when a group grew, else 0."""
    late = data_dir / "late_registrations.csv"
    if disruption["kind"] != "resized" or not late.exists():
        return 0
    with open(late, encoding="utf-8") as f:
        return max(sum(1 for _ in f) - 1, 0)


def coordinator_message(model: EventModel, slice_ids: list[str], effects: dict, disruption: dict,
                        scenario: Optional[dict], data_dir: Path = DATA) -> str:
    nodes = {n.id: n for n in model.nodes}
    origin = disruption["node"]
    grew = disruption["kind"] == "resized"
    payload = {
        "disruption": disruption,
        "chosen_scenario": scenario,
        "affected": [{**nodes[i].model_dump(mode="json"), "effect": effects.get(i, "context")}
                     for i in slice_ids if not (grew and i == origin)],
    }
    rules = ""
    if newcomers_count(disruption, data_dir):
        payload["newcomers"] = {"group": NEWCOMERS_GROUP, "count": newcomers_count(disruption, data_dir),
                                "joining": nodes[origin].label}
        rules = (f"- The group {origin} grew. Do NOT message its existing members. Write one welcome message to "
                 f'the newcomers (group "{NEWCOMERS_GROUP}") and one to each supplier whose quantity changes.\n')
    return (
        'Prepare the update. Reply with {"messages": [...], "revised_schedule": [...], "volunteer_tasks": [...]} '
        "as in the ripple-contracts skill. Do NOT create files: Ripple builds the .ics and .csv itself.\n"
        '- One message per affected group (effect "inform", "resize" or the disruption origin); "group" = the group id. '
        'Say only what changed for that group. status "draft".\n'
        '- "to": the address of a single person (see the attached people.csv), otherwise a short description '
        'like "40 workshop participants".\n'
        f"{rules}"
        '- "actions": the recovery checklist for the organizer team, 3 to 8 items, most urgent first: '
        '{"title", "description", "owner" (a role or team named in the files, e.g. "Volunteer lead", "Organizer", '
        '"Caterer"), "deadline" (a time or day taken from the plan, never invented), "priority" ("high"|"medium"|"low")}. '
        'Include confirming every item of the chosen option\'s "to_confirm" list.\n'
        '- "revised_schedule": every schedule item or commitment whose time or place changes: '
        '{"id": node id, "start": "HH:MM", "end": "HH:MM", "place": "..."}. Empty list if nothing moves.\n'
        f"{dumps(payload)}"
    )


# ---- Setup: event files -> event model ------------------------------------------------

SETUP_FILE_TYPES = {".csv", ".pdf", ".txt", ".json"}
SETUP_MAX_FILES = 5                    # Cominty: at most 5 files per message
SETUP_MAX_BYTES = 10 * 1024 * 1024     # generous for event files; the platform allows more


def setup_message(n_files: int, event_id: str = "codesprint") -> str:
    return f"""Setup. Build the event model for this event from the {n_files} attached files.
Reply with {{"event_model": {{...}}}} as described in the ripple-contracts skill.

Conventions:
- event_id "{event_id}". Reuse the ids given in schedule.json for commitments and program items.
- The venue is a resource with id "venue_<name in snake_case>", with capacity and address in attrs.
- Groups: one per audience that must be informed (attendees, speakers, each speaker with their own
  session, workshop participants, volunteers, the caterer). Ids start with "grp_". Put the real size
  (count the rows in the files) in attrs.size.
- The budget is a constraint node with id "budget" and attrs.remaining_eur.
- Edges (from -> to means a change to `from` can affect `to`):
  located_at: venue -> commitments and sessions held there.
  depends_on: prerequisite commitment -> the program item that needs it.
  scheduled_after: each main program item -> the next one (the workshop runs in parallel, not in the chain).
  must_inform: commitment or program item -> each group that must hear about a change to it.
  sized_by: the attendees group -> the venue and the catering whose size depends on it.
  constrained_by: budget -> the commitments it pays for.
- Put the source file name in "source" for every fact taken from a file."""


def check_setup_files(files: list[tuple[str, bytes]], max_bytes: int = SETUP_MAX_BYTES) -> None:
    if not files:
        raise OrchestratorError("add your event files first")
    if len(files) > SETUP_MAX_FILES:
        raise OrchestratorError(f"at most {SETUP_MAX_FILES} files at a time (a Cominty limit); merge some, e.g. people lists")
    for name, data in files:
        if Path(name).suffix.lower() not in SETUP_FILE_TYPES:
            raise OrchestratorError(f"{name}: only CSV, PDF, TXT or JSON files")
        if len(data) > max_bytes:
            raise OrchestratorError(f"{name} is larger than {max_bytes / 1024 / 1024:g} MB")
        if not data:
            raise OrchestratorError(f"{name} is empty")


def model_checker(parsed: EventModelReply) -> None:
    m = parsed.event_model
    ids = {n.id for n in m.nodes}
    if not any(n.type == NodeType.group for n in m.nodes):
        raise ValueError("the event model has no group: add one group per audience that must be informed")
    bad = [f"{e.from_id}->{e.to_id}" for e in m.edges if e.from_id not in ids or e.to_id not in ids]
    if bad:
        raise ValueError(f"these edges use node ids that do not exist: {bad[:10]}")


async def analyze_event(event: Event, files: list[tuple[str, bytes]], runner: Runner,
                        max_bytes: int = SETUP_MAX_BYTES) -> dict[str, Any]:
    """Files -> Impact Analyst -> event model. Live uploads to Cominty; Replay uses the recorded setup.

    A new model starts the event over: history, cache and costs are cleared.
    """
    check_setup_files(files, max_bytes)
    await runner.stage("setup_started", {"files": [n for n, _ in files], "replay": runner.demo})
    file_ids: list[str] = []
    if not runner.demo:
        for name, data in files:
            file_ids.append((await runner.client.chat.upload_file(data, filename=name)).id)
            await runner.stage("setup_uploaded", {"file": name})
    # live runs are recorded apart, so they never overwrite the recording Replay relies on
    # Replay: the CodeSprint recording ("setup"), or this event's own live recording ("setup-live")
    slug = ("setup" if (runner.fixtures_dir / "setup").exists() else "setup-live") if runner.demo else "setup-live"
    message = setup_message(len(files)) if runner.demo and slug == "setup" and event.id == "demo" \
        else setup_message(len(files), event.id)
    res = await runner.call(slug, "setup_analyst", message, thread_name=f"Event setup · {event.id}",
                            file_ids=file_ids or None, check=model_checker)
    if res.questions:
        q = [x.model_dump() for x in res.questions]
        await runner.stage("setup_questions", {"questions": q})
        return {"status": "questions", "questions": q}
    model = res.data.event_model
    event.set_model(model)
    event.data.update(history=[], cache={}, costs=[], plans=[], analyzed_model=event.data["model"],
                      title=model.title or event.data.get("title", event.id))
    # keep the uploads: recipients, participant lists and the people file are read from them by code
    shutil.rmtree(event.inputs_dir, ignore_errors=True)
    event.inputs_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files:
        (event.inputs_dir / Path(name).name).write_bytes(data)
    event.data["inputs"] = [Path(n).name for n, _ in files]
    add_plan_version(event, "Original plan, from your event files", None)
    log(event, "setup", f"Event analyzed from {len(files)} files" + (" (recorded analysis)" if runner.demo else ""),
        files=[n for n, _ in files])
    add_cost(event, "setup", "impact_analyst", res.cost)
    shutil.rmtree(event.files_dir, ignore_errors=True)
    event.data["setup"] = {"status": "analyzed", "replay": runner.demo, "at": now_iso(),
                           "files": [{"name": n, "bytes": len(d)} for n, d in files],
                           "cost": str(res.cost), "thread_id": res.thread_id}
    event.save()
    await runner.stage("setup_done", {"nodes": len(model.nodes), "edges": len(model.edges)})
    return {"status": "analyzed", "setup": event.data["setup"]}


def confirm_setup(event: Event) -> dict[str, Any]:
    setup = event.data.setdefault("setup", {"status": "not_analyzed"})
    setup["confirmed"] = True
    setup["confirmed_at"] = now_iso()
    log(event, "setup", "Organizer confirmed what Ripple understood")
    event.save()
    return setup


# ---- The flow -----------------------------------------------------------------------


async def handle_disruption(event: Event, text: str, runner: Runner, area: Optional[str] = None) -> dict[str, Any]:
    slug = slugify(text)
    # The same problem reported twice: reopen it instead of running the agents again.
    same = next((h for h in event.data["history"] if h.get("fixture") == slug and h.get("status") == "ready"), None)
    if same is not None:
        await runner.stage("reopened", {"disruption_id": same["id"]})
        return {**same, "reopened": True}
    did = f"d{len(event.data['history']) + 1}-{slug}"
    area = event.data.get("search_area", "") if area is None else area
    model = event.model
    costs: dict[str, str] = {}
    entry: dict[str, Any] = {"id": did, "text": text, "fixture": slug, "at": now_iso()}
    await runner.stage("started", {"disruption_id": did, "text": text})

    res = await runner.call(slug, "impact_analyst", impact_message(model, text),
                            thread_name=f"{text} · Impact Analyst", check=node_checker(model))
    costs["impact_analyst"] = str(res.cost)
    add_cost(event, did, "impact_analyst", res.cost)
    await runner.stage("cost", {"agent": "impact_analyst", "cost": str(res.cost), "total": str(total_cost(event))})
    if res.questions:
        entry.update(status="questions", questions=[q.model_dump() for q in res.questions], costs=costs)
        event.data["history"].append(entry)
        event.save()
        await runner.stage("questions", {"disruption_id": did, "questions": entry["questions"]})
        return entry

    d = res.data.disruption
    dis = d.model_dump(mode="json")
    impact = propagate(model, d)                                   # code
    add_members(model, impact)                                     # code: Speakers -> Speaker 1, 2, 3
    await runner.stage("impact", {"disruption_id": did, "disruption": dis, **impact.to_dict()})
    cache = {a: v["hash"] for a, v in event.data["cache"].items()}
    decision = route(model, impact, cache)                         # code
    await runner.stage("routing", {"disruption_id": did, **decision.to_dict()})

    def cached_output(agent: str) -> Any:
        return event.data["cache"][agent]["output"]

    async def run_agent(agent: str, message: str) -> Optional[BaseModel]:
        await runner.stage("agent_started", {"agent": agent})
        r = await runner.call(slug, agent, message, thread_name=f"{text} · {agent.replace('_', ' ').title()}")
        costs[agent] = str(r.cost)
        add_cost(event, did, agent, r.cost)
        await runner.stage("cost", {"agent": agent, "cost": str(r.cost), "total": str(total_cost(event))})
        if r.questions:   # a later agent asking questions: surface them, keep going without its output
            entry.setdefault("questions", []).extend(q.model_dump() for q in r.questions)
            return None
        event.data["cache"][agent] = {"hash": decision.hashes[agent],
                                      "output": r.data.model_dump(mode="json", by_alias=True)}
        return r.data

    candidates: list[dict] = []
    if "resource_finder" in decision.run:
        out = await run_agent("resource_finder", finder_message(
            model, [n for n in decision.slices["resource_finder"] if impact.effects.get(n) in ("lost", "insufficient")],
            impact.effects, d.detail, d.params.get("new_size"), area, event.data_dir))
        candidates = out.model_dump(mode="json")["candidates"] if out else []
    elif "resource_finder" in decision.cached:
        candidates = cached_output("resource_finder")["candidates"]
    if candidates:
        await runner.stage("candidates", {"disruption_id": did, "candidates": candidates})

    scenarios: list[Scenario] = []
    if "replanner" in decision.run:
        out = await run_agent("replanner", replanner_message(
            model, decision.slices["replanner"], impact.effects, dis, candidates))
        scenarios = out.scenarios if out else []
    elif "replanner" in decision.cached:
        scenarios = [Scenario.model_validate(s) for s in cached_output("replanner")["scenarios"]]

    scenario_dicts, scenarios = settle(model, scenarios, candidates)
    scored = score(scenarios, remaining_budget(model))             # code
    if scenarios:
        await runner.stage("scenarios", {"disruption_id": did, "scored": [x.to_dict() for x in scored]})

    # a resized group keeps its new size for the next disruption
    if d.kind.value == "resized" and d.params.get("new_size"):
        model.node(d.node).attrs["size"] = int(d.params["new_size"])
        event.set_model(model)

    not_run = list(decision.skipped) + list(decision.cached)
    saved = {a: saved_estimate(event, a, runner.fixtures_dir) for a in not_run}
    replanner_used = "replanner" in decision.run or "replanner" in decision.cached
    if "coordinator" in decision.run:
        coordinator = "waiting_for_choice" if replanner_used else "running"
    else:
        coordinator = "cached" if "coordinator" in decision.cached else "skipped"

    entry.update(
        status="ready", disruption=dis, impact=impact.to_dict(), routing=decision.to_dict(),
        candidates=candidates, scenarios=scenario_dicts,
        scored=[x.to_dict() for x in scored], coordinator=coordinator, costs=costs,
        saved=saved, saved_total=str(sum((Decimal(v["cost"]) for v in saved.values() if v["cost"]), Decimal(0))),
        saved_is_estimate=any(v["estimate"] for v in saved.values()),
        outbox=[], files=[],
    )
    event.data["history"].append(entry)
    log(event, "problem", f"Problem reported: {text}", disruption=did)
    event.save()
    await runner.stage("saved", {"disruption_id": did, "saved": saved, "saved_total": entry["saved_total"]})

    # Nothing to choose (no Replanner): inform people right away.
    if coordinator == "running":
        await _coordinate(event, entry, None, runner)
    await runner.stage("done", {"disruption_id": did, "coordinator": entry["coordinator"]})
    return entry


async def choose_scenario(event: Event, scenario_id: Optional[str], runner: Runner,
                          disruption_id: Optional[str] = None) -> dict[str, Any]:
    """Run the Coordinator for the organizer's chosen scenario."""
    history = event.data["history"]
    entry = next((h for h in history if h["id"] == disruption_id), None) if disruption_id else (history or [None])[-1]
    if entry is None or entry.get("status") != "ready":
        raise OrchestratorError("no disruption ready for a choice")
    if entry.get("coordinator") in ("done", "question"):
        raise OrchestratorError(f"a scenario was already chosen for {entry['id']}")
    scenario = None
    if scenario_id is not None:
        scenario = next((s for s in entry["scenarios"] if s["id"] == scenario_id), None)
        if scenario is None:
            raise OrchestratorError(f"unknown scenario {scenario_id!r}")
        info = next(x for x in entry["scored"] if x["id"] == scenario_id)
        if not info["feasible"]:
            raise OrchestratorError(f"scenario {scenario_id} fails a hard limit: {info['violations']}")
    if scenario_id is not None and scenario_id in entry.get("rejected", []):
        raise OrchestratorError(f"option {scenario_id} was rejected")
    await _coordinate(event, entry, scenario, runner)
    if scenario is not None:
        _decide(event, entry, "approved", scenario_id, "")
        event.save()
    await runner.stage("done", {"disruption_id": entry["id"], "coordinator": entry["coordinator"]})
    return {"disruption": entry["id"], "chosen": scenario_id, "status": entry["coordinator"],
            "coordinator_question": entry.get("coordinator_question", []), "outbox": entry["outbox"],
            "revised_schedule": entry["revised_schedule"], "volunteer_tasks": entry["volunteer_tasks"],
            "files": entry["files"], "questions": entry.get("questions", []),
            "cost": entry["costs"].get("coordinator")}


def coordinator_name(scenario: Optional[dict]) -> str:
    """Recording name: one Coordinator reply per option ("coordinator-B"), or "coordinator" when nothing was chosen."""
    return f"coordinator-{scenario['id']}" if scenario else "coordinator"


def recorded_options(entry: dict, fixtures_dir: Path) -> list[str]:
    """Options of a problem whose Coordinator reply is recorded (what Replay can show)."""
    folder = fixtures_dir / entry.get("fixture", "")
    return [s["id"] for s in entry.get("scenarios", []) if (folder / f"coordinator-{s['id']}.json").exists()]


async def _coordinate(event: Event, entry: dict, scenario: Optional[dict], runner: Runner) -> None:
    """Coordinator writes the texts; code builds the files, applies the choice and saves."""
    model = event.model
    routing = entry["routing"]
    effects = entry["impact"]["effects"]
    slice_ids = routing["slices"].get("coordinator") or sorted(n for n, e in effects.items() if e in ("inform", "resize"))
    await runner.stage("agent_started", {"agent": "coordinator"})
    people = event.data_dir / "people.csv"
    file_ids = await runner.upload([people] if people.exists() else [])
    res = await runner.call(entry["fixture"], "coordinator",
                            coordinator_message(model, slice_ids, effects, entry["disruption"], scenario, event.data_dir),
                            thread_name=f"{entry['text']} · Coordinator", file_ids=file_ids or None,
                            name=coordinator_name(scenario))
    if scenario is not None:   # the organizer's choice holds even if the Coordinator asks a question
        model = apply_scenario(model, entry["disruption"]["node"], entry["disruption"]["kind"],
                               Scenario.model_validate(scenario), (scenario.get("venue") or {}).get("name"))
        event.set_model(model)
    entry["chosen"] = scenario["id"] if scenario else None
    await _finish_coordination(event, entry, res, runner)


async def answer_question(event: Event, answer: str, runner: Runner, disruption_id: Optional[str] = None) -> dict[str, Any]:
    """The organizer answers the Coordinator's clarifying question, in the same Cominty thread."""
    history = event.data["history"]
    entry = next((h for h in history if h["id"] == disruption_id), None) if disruption_id else \
        next((h for h in reversed(history) if h.get("coordinator") == "question"), None)
    if entry is None or entry.get("coordinator") != "question":
        raise OrchestratorError("no question is waiting for an answer")
    answer = answer.strip()
    if not answer:
        raise OrchestratorError("write an answer first")
    scenario = next((s for s in entry["scenarios"] if s["id"] == entry.get("chosen")), None)
    await runner.stage("agent_started", {"agent": "coordinator"})
    res = await runner.call(entry["fixture"], "coordinator",
                            f"The organizer answers: {answer}\n\nNow reply with the update JSON as asked before.",
                            thread_name=f"{entry['text']} · Coordinator", thread_id=entry.get("coordinator_thread"),
                            name=f"{coordinator_name(scenario)}-answer-{slugify(answer)[:40]}",
                            extra={"answer": answer})
    entry.setdefault("answers", []).append({"answer": answer, "at": now_iso()})
    await _finish_coordination(event, entry, res, runner)
    await runner.stage("done", {"disruption_id": entry["id"], "coordinator": entry["coordinator"]})
    return {"disruption": entry["id"], "status": entry["coordinator"], "outbox": entry["outbox"],
            "coordinator_question": entry.get("coordinator_question", []), "files": entry["files"]}


def recorded_answers(entry: dict, fixtures_dir: Path) -> list[str]:
    """Answers to the Coordinator's question that Replay can show."""
    if entry.get("coordinator") != "question" or not entry.get("chosen"):
        return []
    folder = fixtures_dir / entry.get("fixture", "")
    out = []
    for f in sorted(folder.glob(f"coordinator-{entry['chosen']}-answer-*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8"))["answer"])
        except (KeyError, ValueError, OSError):
            continue
    return out


async def _finish_coordination(event: Event, entry: dict, res: AgentResult, runner: Runner) -> None:
    """Turn a Coordinator reply into drafts and files, or keep its question for the organizer."""
    model = event.model
    routing = entry["routing"]
    add_cost(event, entry["id"], "coordinator", res.cost)
    entry["costs"]["coordinator"] = str(Decimal(entry["costs"].get("coordinator", "0")) + res.cost)
    entry["coordinator_thread"] = res.thread_id or entry.get("coordinator_thread")
    await runner.stage("cost", {"agent": "coordinator", "cost": str(res.cost), "total": str(total_cost(event))})

    if res.data is None and res.questions:
        q = [x.model_dump() for x in res.questions]
        entry.update(coordinator="question", coordinator_question=q, outbox=[], files=[],
                     revised_schedule=[], volunteer_tasks=[])
        event.save()
        await runner.stage("coordinator_question", {"disruption_id": entry["id"], "questions": q})
        return

    out = res.data.model_dump(mode="json") if res.data else {"messages": [], "revised_schedule": [], "volunteer_tasks": []}
    drafts = out["messages"]
    if entry["disruption"]["kind"] == "resized":   # a grown group: existing members are not messaged (code rule)
        drafts = [m for m in drafts if m["group"] != entry["disruption"]["node"]]
    resolved = []
    for m in drafts:
        # recipients come from the event files (code), the agent's "to" is only kept as a hint
        r = resolve(model, m["group"], event.data_dir)
        people = r["recipients"]
        # a personal message ("to": one email) goes to that person only, if the file lists them in the group
        hint = str(m.get("to", "")).strip().lower()
        one = [p for p in people if p["email"].lower() == hint]
        if one:
            people = one
        resolved.append({**m, "status": "draft", "to_hint": m.get("to", ""),
                         "recipients": people, "recipient_source": r["source"], "edits": []})
    labels = {n.id: n.label for n in model.nodes}
    merged = one_message_per_person(resolved, lambda g: labels.get(g, g))
    messages = [{**m, "id": f"{entry['id']}-m{i}"} for i, m in enumerate(merged, 1)]
    files = build_files(event, entry["id"], model, out["revised_schedule"], messages)
    if "coordinator" in routing.get("hashes", {}) and res.data is not None:
        event.data["cache"]["coordinator"] = {"hash": routing["hashes"]["coordinator"], "output": out}

    entry.update(coordinator="done", coordinator_question=[], outbox=messages,
                 revised_schedule=out["revised_schedule"], volunteer_tasks=out["volunteer_tasks"], files=files,
                 actions=make_actions(entry["id"], out))
    chosen = next((s for s in entry.get("scenarios", []) if s["id"] == entry.get("chosen")), None)
    add_plan_version(event, f"{entry['text']} {chosen['title']}" if chosen else f"{entry['text']} People informed",
                     entry["id"])
    event.save()
    await runner.stage("outbox", {"disruption_id": entry["id"], "outbox": messages, "files": files,
                                  "revised_schedule": out["revised_schedule"],
                                  "volunteer_tasks": out["volunteer_tasks"]})


def build_files(event: Event, disruption_id: str, model: EventModel,
                revised_schedule: list[dict], messages: list[dict]) -> list[dict[str, Any]]:
    """Write <disruption>-revised_schedule.ics and <disruption>-attendee_update.csv for this event."""
    event.files_dir.mkdir(parents=True, exist_ok=True)
    prefix = disruption_id.split("-")[0]          # "d1"
    files = []
    date = event_date(model, event.data_dir)
    if revised_schedule and date:
        name = f"{prefix}-revised_schedule.ics"
        (event.files_dir / name).write_text(build_ics(revised_schedule, model, date), encoding="utf-8", newline="")
        files.append({"name": name, "kind": "ics", "items": len(revised_schedule)})
    participants = event.data_dir / "participants.csv"
    text, people = (build_attendee_csv(participants, model, messages, newcomers_csv=event.data_dir / "late_registrations.csv")
                    if participants.exists() else ("", 0))
    if people:
        name = f"{prefix}-attendee_update.csv"
        (event.files_dir / name).write_text(text, encoding="utf-8", newline="")
        files.append({"name": name, "kind": "csv", "rows": people})
    return files


def make_actions(disruption_id: str, out: dict) -> list[dict[str, Any]]:
    """The Coordinator's checklist; older replies without one fall back to their volunteer tasks."""
    raw = out.get("actions") or [
        {"title": t.get("task", ""), "owner": t.get("who", ""), "deadline": t.get("time", ""), "priority": "medium"}
        for t in out.get("volunteer_tasks", []) if t.get("task")]
    return [{"id": f"{disruption_id}-a{i}", "title": a.get("title", ""), "description": a.get("description", ""),
             "owner": a.get("owner", ""), "deadline": a.get("deadline", ""),
             "priority": a.get("priority") if a.get("priority") in ("high", "medium", "low") else "medium",
             "status": "open", "source": "coordinator" if out.get("actions") else "volunteer task"}
            for i, a in enumerate(raw, 1)]


def set_action(event: Event, action_id: str, done: bool) -> dict[str, Any]:
    for h in event.data["history"]:
        for a in h.get("actions", []):
            if a["id"] == action_id:
                a["status"] = "done" if done else "open"
                a["done_at"] = now_iso() if done else None
                log(event, "action", f"{'Done' if done else 'Reopened'}: {a['title']}", disruption=h["id"], action=action_id)
                event.save()
                return a
    raise OrchestratorError(f"unknown action {action_id!r}")


def _entry(event: Event, disruption_id: str) -> dict[str, Any]:
    entry = next((h for h in event.data["history"] if h["id"] == disruption_id), None)
    if entry is None:
        raise OrchestratorError(f"unknown problem {disruption_id!r}")
    return entry


def _decide(event: Event, entry: dict, kind: str, option: Optional[str], note: str) -> dict[str, Any]:
    d = {"kind": kind, "option": option, "note": note.strip(), "at": now_iso()}
    entry.setdefault("decisions", []).append(d)
    words = {"approved": "Approved", "changes_requested": "Asked for changes to", "rejected": "Rejected"}[kind]
    log(event, "decision", f"{words} option {option}" + (f": {note.strip()}" if note.strip() else ""),
        disruption=entry["id"], option=option, decision=kind)
    return d


def record_decision(event: Event, disruption_id: str, kind: str, option: str, note: str = "") -> dict[str, Any]:
    """Request changes or reject an option (approving goes through choose_scenario)."""
    if kind not in ("changes_requested", "rejected"):
        raise OrchestratorError("a decision is changes_requested or rejected")
    entry = _entry(event, disruption_id)
    if option not in {s["id"] for s in entry.get("scenarios", [])}:
        raise OrchestratorError(f"unknown scenario {option!r}")
    if entry.get("coordinator") in ("done", "question"):
        raise OrchestratorError(f"a scenario was already chosen for {entry['id']}")
    d = _decide(event, entry, kind, option, note)
    if kind == "rejected" and option not in entry.setdefault("rejected", []):
        entry["rejected"].append(option)
    event.save()
    return d


def relax_limits(event: Event, disruption_id: str, budget_eur: Optional[float] = None,
                 capacity_slack: Optional[int] = None) -> dict[str, Any]:
    """The organizer relaxes a hard limit; code re-scores the same options (no agent runs)."""
    entry = _entry(event, disruption_id)
    model = event.model
    relaxed = entry.setdefault("relaxed", {})
    if budget_eur is not None:
        if budget_eur < 0:
            raise OrchestratorError("the budget cannot be negative")
        node = next((n for n in model.nodes if n.type == NodeType.constraint and "remaining_eur" in n.attrs), None)
        if node is None:
            raise OrchestratorError("this event has no budget to change")
        old = node.attrs["remaining_eur"]
        node.attrs["remaining_eur"] = budget_eur
        event.set_model(model)
        relaxed["budget_eur"] = budget_eur
        was = f"EUR {old:g}" if isinstance(old, (int, float)) else "unknown"
        log(event, "constraint", f"Budget left changed from {was} to EUR {budget_eur:g}", disruption=entry["id"])
    if capacity_slack is not None:
        if capacity_slack < 0:
            raise OrchestratorError("the number of people placed elsewhere cannot be negative")
        relaxed["capacity_slack"] = capacity_slack
        log(event, "constraint", f"Accepts up to {capacity_slack} people in an overflow space", disruption=entry["id"])
    scenarios = [Scenario.model_validate(s) for s in entry.get("scenarios", [])]
    entry["scored"] = [x.to_dict() for x in score(scenarios, remaining_budget(event.model),
                                                   relaxed.get("capacity_slack", 0))]
    event.save()
    return entry


async def replan_with(event: Event, disruption_id: str, note: str, runner: Runner) -> dict[str, Any]:
    """The organizer relaxes a limit only new options can use (e.g. another date): the Replanner runs again."""
    entry = _entry(event, disruption_id)
    if entry.get("coordinator") in ("done", "question"):
        raise OrchestratorError(f"a scenario was already chosen for {entry['id']}")
    if not entry.get("scenarios"):
        raise OrchestratorError("this problem has no options to re-plan")
    note = note.strip()
    if not note:
        raise OrchestratorError("say which limit to relax")
    model = event.model
    slice_ids = entry["routing"]["slices"].get("replanner") or sorted(entry["impact"]["effects"])
    message = (replanner_message(model, slice_ids, entry["impact"]["effects"], entry["disruption"],
                                 entry.get("candidates", []))
               + f"\nNone of the earlier options fits all the organizer's limits. The organizer now allows: {note}. "
                 "Propose 2 or 3 new options that use this.")
    await runner.stage("agent_started", {"agent": "replanner"})
    res = await runner.call(entry["fixture"], "replanner", message, thread_name=f"{entry['text']} · Replanner (relaxed)",
                            name=f"replanner-relax-{slugify(note)[:40]}", extra={"relax": note})
    add_cost(event, entry["id"], "replanner", res.cost)
    if res.data is None:
        raise OrchestratorError("the Replanner asked a question instead of proposing options")
    entry["scenarios"], settled = settle(model, res.data.scenarios, entry.get("candidates", []))
    entry["scored"] = [x.to_dict() for x in score(settled, remaining_budget(model),
                                                   entry.get("relaxed", {}).get("capacity_slack", 0))]
    entry["rejected"] = []
    entry.setdefault("relaxed", {})["other"] = note
    log(event, "constraint", f"Allows: {note} (new options prepared)", disruption=entry["id"])
    event.save()
    return entry


def edit_message(event: Event, message_id: str, subject: str, body: str) -> dict[str, Any]:
    """The organizer edits a draft before approving it; every earlier version is kept."""
    for h in event.data["history"]:
        for m in h.get("outbox", []):
            if m["id"] != message_id:
                continue
            if m["status"] != "draft":
                raise OrchestratorError("only drafts can be edited")
            subject, body = subject.strip(), body.strip()
            if not subject or not body:
                raise OrchestratorError("a message needs a subject and a text")
            if subject == m.get("subject") and body == m.get("body"):
                return m
            m.setdefault("edits", []).append({"at": now_iso(), "subject": m.get("subject", ""), "body": m.get("body", "")})
            m.update(subject=subject, body=body, edited=True)
            log(event, "edit", f"Edited the message to {m['group']}: {subject}", disruption=h["id"], message=message_id)
            event.save()
            return m
    raise OrchestratorError(f"unknown message ids: ['{message_id}']")


def set_budget(event: Event, remaining_eur: float) -> None:
    """A budget change for the whole event (from the What-if): re-score every problem still open."""
    model = event.model
    node = next((n for n in model.nodes if n.type == NodeType.constraint and "remaining_eur" in n.attrs), None)
    if node is None:
        raise OrchestratorError("this event has no budget to change")
    old = node.attrs["remaining_eur"]
    node.attrs["remaining_eur"] = remaining_eur
    event.set_model(model)
    was = f"EUR {old:g}" if isinstance(old, (int, float)) else "unknown"
    log(event, "constraint", f"Budget left changed from {was} to EUR {remaining_eur:g} (from the What-if)")
    for h in event.data["history"]:
        if h.get("scenarios") and h.get("coordinator") == "waiting_for_choice":
            slack = h.get("relaxed", {}).get("capacity_slack", 0)
            h["scored"] = [x.to_dict() for x in score([Scenario.model_validate(s) for s in h["scenarios"]],
                                                       remaining_eur, slack)]
    event.save()


def add_members(model: EventModel, impact) -> None:
    """When a group is affected, its sub-groups are too (they get the group's news), one wave later.
    Header, ripple map and outbox then agree: Speaker 1 isn't "unchanged" when the Speakers are informed."""
    i = 0
    while i < len(impact.waves):
        added = [c for g in impact.waves[i] for c in member_groups(model, g) if c not in impact.effects]
        added = list(dict.fromkeys(added))
        for c in added:
            impact.effects[c] = "inform"
        if added:
            if i + 1 < len(impact.waves):
                impact.waves[i + 1].extend(added)
            else:
                impact.waves.append(added)
        i += 1


def settle(model: EventModel, scenarios: list[Scenario], candidates: list[dict]) -> tuple[list[dict], list[Scenario]]:
    """One room per venue option (see rooms.py); returns the stored dicts and the scenarios to score."""
    gid = attendee_group(model)
    size = model.node(gid).attrs.get("size") if gid else None
    dicts = settle_rooms([s.model_dump(mode="json") for s in scenarios], candidates, size)
    return dicts, [Scenario.model_validate(x) for x in dicts]


def approve_messages(event: Event, ids: list[str]) -> list[dict[str, Any]]:
    """Mark outbox drafts as approved. Nothing is sent: export or copy happens in the UI."""
    wanted = set(ids)
    found = [m for h in event.data["history"] for m in h.get("outbox", []) if m["id"] in wanted]
    missing = wanted - {m["id"] for m in found}
    if missing:   # check everything first, change nothing on error
        raise OrchestratorError(f"unknown message ids: {sorted(missing)}")
    for m in found:
        m["status"] = "approved"
        m["approved_at"] = now_iso()
    log(event, "approval", f"Approved {len(found)} message{'s' if len(found) != 1 else ''}", messages=sorted(wanted))
    event.save()
    return found
