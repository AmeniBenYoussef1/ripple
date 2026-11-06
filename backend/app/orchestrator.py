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

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel

from .agents import (AgentResult, CandidatesReply, CoordinatorReply, DisruptionReply,
                     EventCallback, Question, ScenariosReply, call_agent)
from .exports import build_attendee_csv, build_ics
from .impact_engine import propagate, route
from .models import EventModel, NodeType, Scenario
from .scoring import score

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND / "fixtures"
EVENTS = BACKEND / "events"
DATA = BACKEND / "demo_data"
GENERATED_MODEL = DATA / "event_model_generated.json"

# engine agent name -> (agents.py key, reply shape, max tool rounds)
SPEC: dict[str, tuple[str, type[BaseModel], int]] = {
    "impact_analyst": ("impact", DisruptionReply, 6),
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
        return self.path.parent / self.id / "files"

    def set_model(self, model: EventModel) -> None:
        self.data["model"] = model.model_dump(mode="json", by_alias=True)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, default=str) + "\n", encoding="utf-8")


def new_event(event_id: str, model_path: Path = GENERATED_MODEL, events_dir: Path = EVENTS,
              search_area: str = "") -> Event:
    """Start (or reset) an event from a saved model file."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", event_id):
        raise OrchestratorError("event id: use 1-64 letters, digits, '-' or '_'")
    if not model_path.exists():
        raise OrchestratorError(f"no saved event model at {model_path}; run scripts/try_setup.py first")
    model = EventModel.model_validate(json.loads(model_path.read_text(encoding="utf-8")))
    ev = Event(event_id, events_dir / f"{event_id}.json",
               {"event_id": event_id, "search_area": search_area, "model": None,
                "history": [], "cache": {}, "costs": []})
    shutil.rmtree(ev.files_dir.parent, ignore_errors=True)
    ev.set_model(model)
    ev.save()
    return ev


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

    def _fixture(self, slug: str, agent: str) -> Path:
        return self.fixtures_dir / slug / f"{agent}.json"

    async def stage(self, name: str, payload: dict[str, Any]) -> None:
        if self.on_stage is not None:
            out = self.on_stage(name, payload)
            if hasattr(out, "__await__"):
                await out

    async def call(self, slug: str, agent: str, message: str, *, thread_name: str,
                   file_ids: Optional[list[str]] = None, check=None) -> AgentResult:
        key, shape, max_steps = SPEC[agent]
        path = self._fixture(slug, agent)
        if self.demo or (self.prefer_recordings and path.exists()):
            if not path.exists():
                raise OrchestratorError(f"DEMO_MODE: no recording at {path.relative_to(self.fixtures_dir.parent)}")
            return await self._replay(path, key, shape)
        if self.client is None:
            raise OrchestratorError("no Cominty client (set DEMO_MODE=true to replay recordings)")
        res = await call_agent(self.client, key, message, shape, file_ids=file_ids, on_event=self.on_event,
                               thread_name=thread_name, max_steps=max_steps, check=check)
        self._record(path, agent, message, res)
        return res

    async def _replay(self, path: Path, key: str, shape: type[BaseModel]) -> AgentResult:
        rec = json.loads(path.read_text(encoding="utf-8"))
        res = AgentResult(agent=key, cost=Decimal(rec["cost"]), thread_id=rec.get("thread_id"),
                          raw=rec.get("raw", ""),
                          questions=[Question.model_validate(q) for q in rec.get("questions", [])])
        if rec.get("data") is not None:
            res.data = shape.model_validate(rec["data"])
        if self.on_event is not None:
            out = self.on_event({"agent": key, "event": "Replay", "status": "success",
                                 "description": f"replayed recording {path.parent.name}/{path.name}", "id": None})
            if hasattr(out, "__await__"):
                await out
        return res

    def _record(self, path: Path, agent: str, message: str, res: AgentResult) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"agent": agent, "recorded_at": now_iso(), "thread_id": res.thread_id, "cost": str(res.cost),
               "message": message, "raw": res.raw,
               "data": res.data.model_dump(mode="json", by_alias=True) if res.data else None,
               "questions": [q.model_dump() for q in res.questions],
               "agent_files": [{"name": f.name, "has_url": bool(getattr(f, "url", ""))} for f in res.files]}
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    async def upload(self, paths: list[Path]) -> list[str]:
        if self.demo or self.client is None:
            return []
        return [(await self.client.chat.upload_file(p)).id for p in paths]


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


def event_date(model: EventModel) -> Optional[str]:
    for n in model.nodes:
        if n.attrs.get("date"):
            return str(n.attrs["date"])
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


def add_cost(event: Event, disruption_id: str, agent: str, cost: Decimal) -> None:
    event.data["costs"].append({"disruption": disruption_id, "agent": agent, "cost": str(cost)})


def total_cost(event: Event) -> Decimal:
    return sum((Decimal(c["cost"]) for c in event.data["costs"]), Decimal(0))


def apply_scenario(model: EventModel, origin: str, kind: str, s: Scenario) -> EventModel:
    """Write the chosen scenario back into the model, so the next disruption sees it."""
    nodes = {n.id: n for n in model.nodes}
    for ch in s.changes:
        n = nodes.get(ch.get("node"))
        if n is None:
            continue
        if isinstance(ch.get("set"), dict):
            n.attrs.update(ch["set"])
        if ch.get("replace_with"):
            n.attrs["replaced_by"] = ch["replace_with"]
    if kind == "lost" and origin in nodes and s.capacity is not None:
        nodes[origin].attrs["capacity"] = s.capacity   # the replacement venue's capacity
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
                   new_size: Optional[int], area: str) -> str:
    nodes = {n.id: n for n in model.nodes}
    needs = [{"resource": nodes[r].model_dump(mode="json"), "problem": effects[r],
              "requirements": {"min_capacity": needed_capacity(model, r, new_size), "date": event_date(model),
                               "area": area, "max_extra_cost_eur": remaining_budget(model)}}
             for r in finder_nodes]
    return (
        'Find replacements. Reply with {"candidates": [...]} (3 to 5) as in the ripple-contracts skill. '
        'Search the web; every candidate needs its source url; availability is always "to confirm". '
        "Prefer places that fit min_capacity and are in or near the area.\n"
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


def coordinator_message(model: EventModel, slice_ids: list[str], effects: dict, disruption: dict,
                        scenario: Optional[dict]) -> str:
    nodes = {n.id: n for n in model.nodes}
    payload = {
        "disruption": disruption,
        "chosen_scenario": scenario,
        "affected": [{**nodes[i].model_dump(mode="json"), "effect": effects.get(i, "context")} for i in slice_ids],
    }
    return (
        'Prepare the update. Reply with {"messages": [...], "revised_schedule": [...], "volunteer_tasks": [...]} '
        "as in the ripple-contracts skill. Do NOT create files: Ripple builds the .ics and .csv itself.\n"
        '- One message per affected group (effect "inform", "resize" or the disruption origin); "group" = the group id. '
        'Say only what changed for that group. status "draft".\n'
        '- "to": the address of a single person (see the attached people.csv), otherwise a short description '
        'like "40 workshop participants".\n'
        '- "revised_schedule": every schedule item or commitment whose time or place changes: '
        '{"id": node id, "start": "HH:MM", "end": "HH:MM", "place": "..."}. Empty list if nothing moves.\n'
        f"{dumps(payload)}"
    )


# ---- The flow -----------------------------------------------------------------------


async def handle_disruption(event: Event, text: str, runner: Runner, area: Optional[str] = None) -> dict[str, Any]:
    slug = slugify(text)
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
            impact.effects, d.detail, d.params.get("new_size"), area))
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

    scored = score(scenarios, remaining_budget(model))             # code
    if scenarios:
        await runner.stage("scenarios", {"disruption_id": did, "scored": [x.to_dict() for x in scored]})

    # a resized group keeps its new size for the next disruption
    if d.kind.value == "resized" and d.params.get("new_size"):
        model.node(d.node).attrs["size"] = int(d.params["new_size"])
        event.set_model(model)

    not_run = list(decision.skipped) + list(decision.cached)
    saved = {a: (str(avg_cost(event, a)) if avg_cost(event, a) is not None else None) for a in not_run}
    replanner_used = "replanner" in decision.run or "replanner" in decision.cached
    if "coordinator" in decision.run:
        coordinator = "waiting_for_choice" if replanner_used else "running"
    else:
        coordinator = "cached" if "coordinator" in decision.cached else "skipped"

    entry.update(
        status="ready", disruption=dis, impact=impact.to_dict(), routing=decision.to_dict(),
        candidates=candidates, scenarios=[s.model_dump(mode="json") for s in scenarios],
        scored=[x.to_dict() for x in scored], coordinator=coordinator, costs=costs,
        saved=saved, saved_total=str(sum((Decimal(v) for v in saved.values() if v), Decimal(0))),
        outbox=[], files=[],
    )
    event.data["history"].append(entry)
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
    if entry.get("coordinator") == "done":
        raise OrchestratorError(f"a scenario was already chosen for {entry['id']}")
    scenario = None
    if scenario_id is not None:
        scenario = next((s for s in entry["scenarios"] if s["id"] == scenario_id), None)
        if scenario is None:
            raise OrchestratorError(f"unknown scenario {scenario_id!r}")
        info = next(x for x in entry["scored"] if x["id"] == scenario_id)
        if not info["feasible"]:
            raise OrchestratorError(f"scenario {scenario_id} fails a hard limit: {info['violations']}")
    await _coordinate(event, entry, scenario, runner)
    await runner.stage("done", {"disruption_id": entry["id"], "coordinator": "done"})
    return {"disruption": entry["id"], "chosen": scenario_id, "outbox": entry["outbox"],
            "revised_schedule": entry["revised_schedule"], "volunteer_tasks": entry["volunteer_tasks"],
            "files": entry["files"], "questions": entry.get("questions", []),
            "cost": entry["costs"].get("coordinator")}


async def _coordinate(event: Event, entry: dict, scenario: Optional[dict], runner: Runner) -> None:
    """Coordinator writes the texts; code builds the files, applies the choice and saves."""
    model = event.model
    routing = entry["routing"]
    effects = entry["impact"]["effects"]
    slice_ids = routing["slices"].get("coordinator") or sorted(n for n, e in effects.items() if e in ("inform", "resize"))
    await runner.stage("agent_started", {"agent": "coordinator"})
    file_ids = await runner.upload([DATA / "people.csv"])
    res = await runner.call(entry["fixture"], "coordinator",
                            coordinator_message(model, slice_ids, effects, entry["disruption"], scenario),
                            thread_name=f"{entry['text']} · Coordinator", file_ids=file_ids or None)
    add_cost(event, entry["id"], "coordinator", res.cost)
    entry["costs"]["coordinator"] = str(res.cost)
    await runner.stage("cost", {"agent": "coordinator", "cost": str(res.cost), "total": str(total_cost(event))})

    if scenario is not None:
        model = apply_scenario(model, entry["disruption"]["node"], entry["disruption"]["kind"],
                               Scenario.model_validate(scenario))
        event.set_model(model)

    out = res.data.model_dump(mode="json") if res.data else {"messages": [], "revised_schedule": [], "volunteer_tasks": []}
    messages = [{**m, "id": f"{entry['id']}-m{i}", "status": "draft"} for i, m in enumerate(out["messages"], 1)]
    files = build_files(event, entry["id"], model, out["revised_schedule"], messages)
    if "coordinator" in routing.get("hashes", {}) and res.data is not None:
        event.data["cache"]["coordinator"] = {"hash": routing["hashes"]["coordinator"], "output": out}

    entry.update(chosen=scenario["id"] if scenario else None, coordinator="done", outbox=messages,
                 revised_schedule=out["revised_schedule"], volunteer_tasks=out["volunteer_tasks"], files=files,
                 questions=entry.get("questions", []) + [q.model_dump() for q in res.questions])
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
    date = event_date(model)
    if revised_schedule and date:
        name = f"{prefix}-revised_schedule.ics"
        (event.files_dir / name).write_text(build_ics(revised_schedule, model, date), encoding="utf-8", newline="")
        files.append({"name": name, "kind": "ics", "items": len(revised_schedule)})
    text, people = build_attendee_csv(DATA / "participants.csv", model, messages)
    if people:
        name = f"{prefix}-attendee_update.csv"
        (event.files_dir / name).write_text(text, encoding="utf-8", newline="")
        files.append({"name": name, "kind": "csv", "rows": people})
    return files


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
    event.save()
    return found
