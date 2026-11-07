"""Ripple HTTP API (FastAPI) on top of the orchestrator.

Run:  cd backend && .venv/Scripts/uvicorn app.main:app --reload --port 8000
With DEMO_MODE=true in .env, agent replies are replayed from fixtures/ (no Cominty calls).

Live progress goes to GET /api/events/{id}/stream as Server-Sent Events:
  started, agent (ToolCall / LlmStep / Replay with a description), cost, impact (waves),
  routing (run / skipped / cached), candidates, scenarios, saved, outbox, questions, done, error.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import secrets
import time
import zipfile
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .agents import AgentOutputError
from .geo import Geocoder, venue_pins
from .visitors import Limits, Visitors, is_visitor_event, new_visitor_event_id
from .whatif import simulate
from .orchestrator import (check_setup_files, list_events, reset_problems, set_budget, slugify, edit_message, replan_with, record_decision, relax_limits, set_action, answer_question, recorded_answers, recorded_options, DATA, EVENTS, FIXTURES, GENERATED_MODEL, SETUP_MAX_BYTES, Event, OrchestratorError,
                           Runner, analyze_event, approve_messages, choose_scenario, confirm_setup, demo_mode,
                           handle_disruption, load_event, new_event, total_cost)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DEFAULT_AREA = os.environ.get("RIPPLE_SEARCH_AREA", "Paris, France")
# Events that always replay their recordings in fixtures/, even on a Live server (the CodeSprint demo).
REPLAY_EVENTS = {e.strip() for e in os.environ.get("RIPPLE_REPLAY_EVENTS", "demo").split(",") if e.strip()}
RECORDINGS = Path(__file__).resolve().parents[1] / "recordings"   # Live recordings of every other event
SAMPLE_PACK = Path(__file__).resolve().parents[1] / "sample_pack"   # fictional files visitors can download
PUBLIC = os.environ.get("RIPPLE_PUBLIC", "false").strip().lower() in ("1", "true", "yes")
COOKIE = "ripple_visitor"
# Plain words for agent failures on the visitor's own files and problems
NOT_AN_EVENT = ("Ripple couldn't find an event in these files: no groups of people, schedule or venue. "
                "Upload your participant list, schedule, contracts or budget, or try a sample event.")
VAGUE = ("Ripple couldn't tell what happened. Try one sentence about what went wrong, to what and when, "
         "e.g. \u201cThe caterer can only deliver at 13:30.\u201d")
FILE_TYPES = {".ics": "text/calendar", ".csv": "text/csv"}
# the five fictional demo files, offered on the setup page ("Use the demo files")
DEMO_FILES = ["participants.csv", "schedule.json", "catering_contract.txt", "people.csv", "budget.json"]
DEMO_TYPES = {".csv": "text/csv", ".json": "application/json", ".txt": "text/plain"}


def replay_delay_from_env() -> tuple[float, float]:
    """DEMO_REPLAY_DELAY="0.3-0.8" (default) or "0" to replay instantly."""
    raw = os.environ.get("DEMO_REPLAY_DELAY", "0.3-0.8").strip()
    lo, _, hi = raw.partition("-")
    try:
        return (float(lo), float(hi or lo))
    except ValueError:
        return (0.3, 0.8)


# ---- Request bodies -------------------------------------------------------------


class SetupBody(BaseModel):
    search_area: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=120)


class DisruptionBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class AnswerBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    disruption: Optional[str] = None


class ActionBody(BaseModel):
    done: bool


class DecisionBody(BaseModel):
    kind: str = Field(pattern="^(changes_requested|rejected)$")
    option: str
    note: str = Field(default="", max_length=1000)


class RelaxBody(BaseModel):
    budget_eur: Optional[float] = None
    capacity_slack: Optional[int] = None


class ReplanBody(BaseModel):
    note: str = Field(min_length=1, max_length=300)


class EditBody(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=10000)


class WhatIfBody(BaseModel):
    budget_eur: Optional[float] = None
    attendees: Optional[int] = None
    delay_node: Optional[str] = None
    delay_minutes: int = 0
    problem: Optional[str] = None


class BudgetBody(BaseModel):
    remaining_eur: float = Field(ge=0)


class ApproveBody(BaseModel):
    ids: list[str] = Field(min_length=1)


# ---- Live event stream (SSE) --------------------------------------------------------


class Broker:
    """Keeps every event's progress log and fans it out to open /stream connections."""

    def __init__(self) -> None:
        self.log: dict[str, list[dict[str, Any]]] = {}
        self.queues: dict[str, list[asyncio.Queue]] = {}

    def publish(self, event_id: str, kind: str, data: dict[str, Any]) -> None:
        msg = {"type": kind, "data": data}
        self.log.setdefault(event_id, []).append(msg)
        for q in self.queues.get(event_id, []):
            q.put_nowait(msg)

    def clear(self, event_id: str) -> None:
        self.log.pop(event_id, None)

    def subscribe(self, event_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.queues.setdefault(event_id, []).append(q)
        return q

    def unsubscribe(self, event_id: str, q: asyncio.Queue) -> None:
        if q in self.queues.get(event_id, []):
            self.queues[event_id].remove(q)


def sse(msg: dict[str, Any]) -> str:
    return f"event: {msg['type']}\ndata: {json.dumps(msg['data'], default=str, ensure_ascii=False)}\n\n"


# ---- App factory ---------------------------------------------------------------------


def create_app(events_dir: Path = EVENTS, fixtures_dir: Path = FIXTURES, model_path: Path = GENERATED_MODEL,
               demo: Optional[bool] = None, replay_delay: Optional[tuple[float, float]] = None,
               geocoder: Optional[Geocoder] = None, recordings_dir: Path = RECORDINGS,
               replay_events: Optional[set[str]] = None, public: Optional[bool] = None,
               visitors: Optional[Visitors] = None, client: Any = None) -> FastAPI:
    demo = demo_mode() if demo is None else demo
    replay_delay = replay_delay_from_env() if replay_delay is None else replay_delay
    replay_events = REPLAY_EVENTS if replay_events is None else replay_events
    public = PUBLIC if public is None else public
    visitors = visitors or Visitors(events_dir / "_visitors" / "usage.json", Limits.from_env())
    sample_of: dict[str, Optional[str]] = {}

    def replay_of(event_id: str) -> Optional[str]:
        """A visitor's private copy of a sample event replays that sample's recordings."""
        if event_id not in sample_of:
            p = events_dir / f"{event_id}.json"
            sample_of[event_id] = json.loads(p.read_text(encoding="utf-8")).get("replay_of") if p.exists() else None
        return sample_of[event_id]

    def is_replay(event_id: str) -> bool:
        return demo or event_id in replay_events or replay_of(event_id) in replay_events

    def recordings_for(event_id: str) -> Path:
        """CodeSprint replays fixtures/; every other event has its own folder, so nothing is overwritten."""
        if event_id in replay_events or replay_of(event_id) in replay_events:
            return fixtures_dir
        return recordings_dir / event_id

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        visitors.cleanup(events_dir, recordings_dir)
        if client is not None:          # tests pass a fake client
            app.state.client = client
            yield
            return
        if demo:
            app.state.client = None
            yield
            return
        from cominty_sdk import AsyncCominty
        async with AsyncCominty() as live_client:
            app.state.client = live_client
            yield

    app = FastAPI(title="Ripple API", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    last_cleanup = [time.time()]

    @app.middleware("http")
    async def visitor_identity(request: Request, call_next):
        """A cookie per browser; a visitor's events are theirs only; shared events are read-only in public."""
        vid = request.cookies.get(COOKIE, "")
        fresh = None
        if not re.fullmatch(r"[a-f0-9]{32}", vid):
            vid = fresh = secrets.token_hex(16)
        request.state.visitor = vid
        request.state.ip = request.client.host if request.client else "unknown"
        if request.method == "OPTIONS":                         # CORS preflights carry no cookie
            return await call_next(request)
        m = re.match(r"^/api/events/([^/]+)", request.url.path)
        if m:
            eid = m.group(1)
            if is_visitor_event(eid) and visitors.owner(eid) != vid:
                return JSONResponse({"detail": f"unknown event {eid!r}"}, status_code=404)
            if public and not is_visitor_event(eid) and request.method != "GET":
                return JSONResponse({"detail": "public|In the public demo you work on your own copy. "
                                               "Start from the Start page or open a sample event."}, status_code=403)
        if time.time() - last_cleanup[0] > 600:                 # remove old visitor events now and then
            last_cleanup[0] = time.time()
            visitors.cleanup(events_dir, recordings_dir)
        response = await call_next(request)
        if fresh:
            # frontend and backend on different sites (e.g. Vercel + Render) need SameSite=None; Secure
            cross = os.environ.get("RIPPLE_CROSS_SITE_COOKIE", "false").lower() == "true"
            response.set_cookie(COOKIE, vid, max_age=30 * 24 * 3600, httponly=True,
                                samesite="none" if cross else "lax", secure=cross)
        return response

    def live_gate(request: Request, event_id: str, kind: str) -> bool:
        """Before agents run for real: limits for visitors; nothing live on shared events in public.
        Returns True when the run is live and its cost must be added to the day's total."""
        if is_replay(event_id):
            return False
        if app.state.client is None:
            raise HTTPException(403, "live-off|Live mode is off on this server, so Ripple can only replay "
                                     "recorded events. Try a sample event.")
        if is_visitor_event(event_id):
            why = visitors.refusal(request.state.visitor, request.state.ip, kind)
            if why:
                raise HTTPException(429, "limit|" + why)
            return True
        if public:
            raise HTTPException(403, "public|In the public demo, live agents only run on your own event.")
        return False

    async def counted(request: Request, event_id: str, kind: str, work):
        """Run live work for a visitor: count the try, and add the real cost to today's total."""
        live = live_gate(request, event_id, kind)
        if not live:
            return await work()
        before = total_cost(get_event(event_id))
        result = None
        try:
            result = await work()
            return result
        finally:
            spent = total_cost(get_event(event_id)) - before
            visitors.add_cost(max(spent, Decimal(0)) if kind != "analyses" else total_cost(get_event(event_id)))
            reopened = isinstance(result, dict) and result.get("reopened")
            if kind in ("analyses", "problems") and not reopened:
                visitors.count(request.state.visitor, request.state.ip, kind)
    broker = Broker()
    geo = geocoder or Geocoder()
    locks: dict[str, asyncio.Lock] = {}
    app.state.broker = broker

    # -- helpers --

    def get_event(event_id: str) -> Event:
        try:
            return load_event(event_id, events_dir=events_dir)
        except OrchestratorError as err:
            raise HTTPException(404, str(err)) from err

    def runner_for(event_id: str) -> Runner:
        replay = is_replay(event_id)
        return Runner(client=app.state.client, demo=replay, fixtures_dir=recordings_for(event_id),
                      replay_delay=replay_delay if replay else (0.0, 0.0),
                      on_event=lambda e: broker.publish(event_id, "agent", e),
                      on_stage=lambda name, payload: broker.publish(event_id, name, payload))

    def state(event: Event) -> dict[str, Any]:
        saved = sum((Decimal(h.get("saved_total", "0")) for h in event.data["history"]), Decimal(0))
        files = sorted(p.name for p in event.files_dir.glob("*")) if event.files_dir.exists() else []
        # Replay: which options have a recorded Coordinator reply (others are "Live mode only")
        rec = recordings_for(event.id)
        replay = is_replay(event.id)
        replayable = ({h["id"]: {"options": recorded_options(h, rec), "answers": recorded_answers(h, rec)}
                       for h in event.data["history"]} if replay else None)
        data = {k: v for k, v in event.data.items() if k != "analyzed_model"}
        return {**data, "demo_mode": replay, "replayable": replayable, "visitor": is_visitor_event(event.id), "total_cost": str(total_cost(event)),
                "saved_total": str(saved), "files": files}

    async def guarded(event_id: str, work, agent_error: Optional[str] = None):
        """One flow at a time per event; agent and platform errors become clear HTTP errors."""
        lock = locks.setdefault(event_id, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "this event is busy with another request")
        async with lock:
            try:
                return await work()
            except OrchestratorError as err:
                broker.publish(event_id, "error", {"message": str(err)})
                raise HTTPException(400, str(err)) from err
            except AgentOutputError as err:
                broker.publish(event_id, "error", {"message": str(err)})
                if agent_error:
                    raise HTTPException(422, agent_error) from err
                raise HTTPException(502, str(err)) from err
            except Exception as err:  # Cominty SDK errors (rate limit, auth, connection, ...)
                name = type(err).__name__
                if not any(c.__name__ == "ComintyError" for c in type(err).__mro__):
                    raise
                status = 429 if name == "RateLimitError" else 502
                msg = f"Cominty {name}: {err}"
                broker.publish(event_id, "error", {"message": msg})
                raise HTTPException(status, msg) from err

    # -- routes --

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "demo_mode": demo}

    @app.get("/api/events")
    async def events(request: Request) -> list[dict[str, Any]]:
        mine = set(visitors.events_of(request.state.visitor))
        return [{**e, "replay": is_replay(e["id"])} for e in list_events(events_dir)
                if ((e["id"] in mine) if (is_visitor_event(e["id"]) or public) else True)]

    # ---- the public Start flow --------------------------------------------------------

    @app.get("/api/visitor/status")
    async def visitor_status(request: Request) -> dict[str, Any]:
        return {"public": public, "live": app.state.client is not None,
                **visitors.status(request.state.visitor, request.state.ip),
                "events": visitors.events_of(request.state.visitor)}

    @app.post("/api/visitor/event")
    async def visitor_event(request: Request, body: Optional[SetupBody] = None) -> dict[str, Any]:
        """A new event of this visitor's own (never one of the shared events)."""
        eid = new_visitor_event_id()
        title = (body.title if body and body.title else None) or "My event"
        event = new_event(eid, model_path=None, events_dir=events_dir, title=title,
                          search_area=(body.search_area if body and body.search_area else None) or DEFAULT_AREA)
        visitors.own(eid, request.state.visitor)
        return state(event)

    @app.post("/api/visitor/sample")
    async def visitor_sample(request: Request) -> dict[str, Any]:
        """A private copy of the CodeSprint sample: it replays the recorded runs, for free."""
        eid = new_visitor_event_id()
        event = new_event(eid, model_path=model_path, events_dir=events_dir, search_area=DEFAULT_AREA,
                          title="CodeSprint 2026 (sample)")
        event.data["replay_of"] = "demo"
        event.save()
        sample_of[eid] = "demo"
        visitors.own(eid, request.state.visitor)
        return state(event)

    @app.get("/api/sample-pack.zip")
    async def sample_pack() -> Response:
        """The fictional HerCode files, to try Ripple with your own upload."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(SAMPLE_PACK.glob("*")):
                if f.is_file():
                    z.write(f, f"ripple-sample-pack/{f.name}")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="ripple-sample-pack.zip"'})

    @app.post("/api/events/{event_id}/create")
    async def create(event_id: str, body: Optional[SetupBody] = None) -> dict[str, Any]:
        """A new, empty event: it gets its model from its own files on the setup page."""
        if event_id in replay_events:
            raise HTTPException(400, f"{event_id!r} is reserved for the CodeSprint demo")
        if (events_dir / f"{event_id}.json").exists():
            raise HTTPException(409, f"an event called {event_id!r} already exists")
        area = (body.search_area if body and body.search_area else None) or DEFAULT_AREA
        title = body.title if body and body.title else event_id
        try:
            event = new_event(event_id, model_path=None, events_dir=events_dir, search_area=area, title=title)
        except OrchestratorError as err:
            raise HTTPException(400, str(err)) from err
        return state(event)

    @app.post("/api/events/{event_id}/setup")
    async def setup(event_id: str, body: Optional[SetupBody] = None) -> dict[str, Any]:
        area = (body.search_area if body and body.search_area else None) or DEFAULT_AREA
        try:
            # the CodeSprint demo starts from its saved model; any other event starts blank
            event = new_event(event_id, model_path=model_path if event_id in replay_events else None,
                              events_dir=events_dir, search_area=area)
        except OrchestratorError as err:
            raise HTTPException(400, str(err)) from err
        broker.clear(event_id)
        return state(event)

    @app.post("/api/events/{event_id}/reset")
    async def reset(event_id: str) -> dict[str, Any]:
        current = get_event(event_id)
        area = current.data.get("search_area") or DEFAULT_AREA
        if current.data.get("replay_of"):
            event = new_event(event_id, model_path=model_path, events_dir=events_dir, search_area=area,
                              title=current.data.get("title", ""))
            event.data["replay_of"] = current.data["replay_of"]
            event.save()
            broker.clear(event_id)
            broker.publish(event_id, "reset", {})
            return state(event)
        if event_id not in replay_events and current.data.get("analyzed_model"):
            event = reset_problems(current)          # same files and analysis: cheap retakes
        else:
            event = new_event(event_id, model_path=model_path if event_id in replay_events else None,
                              events_dir=events_dir, search_area=area, title=current.data.get("title", ""))
        broker.clear(event_id)
        broker.publish(event_id, "reset", {})
        return state(event)

    @app.get("/api/events/{event_id}")
    async def get_state(event_id: str) -> dict[str, Any]:
        return state(get_event(event_id))

    @app.post("/api/events/{event_id}/disruptions")
    async def disruption(event_id: str, body: DisruptionBody, request: Request) -> dict[str, Any]:
        event = get_event(event_id)
        if not body.text.strip():
            raise HTTPException(400, "write what went wrong first")
        return await counted(request, event_id, "problems", lambda: guarded(
            event_id, lambda: handle_disruption(event, body.text.strip(), runner_for(event_id)), "vague|" + VAGUE))

    @app.post("/api/events/{event_id}/scenarios/{scenario_id}/choose")
    async def choose(event_id: str, scenario_id: str, request: Request) -> dict[str, Any]:
        event = get_event(event_id)
        return await counted(request, event_id, "other", lambda: guarded(
            event_id, lambda: choose_scenario(event, scenario_id, runner_for(event_id))))

    @app.post("/api/events/{event_id}/answer")
    async def answer(event_id: str, body: AnswerBody, request: Request) -> dict[str, Any]:
        """Answer the Coordinator's clarifying question (same Cominty thread)."""
        event = get_event(event_id)
        return await counted(request, event_id, "other", lambda: guarded(
            event_id, lambda: answer_question(event, body.text, runner_for(event_id), body.disruption)))

    def plain(event_id: str, work):
        """Small code-only changes: no agent runs, errors become 400."""
        try:
            return work()
        except OrchestratorError as err:
            raise HTTPException(400, str(err)) from err

    @app.post("/api/events/{event_id}/actions/{action_id}")
    async def action(event_id: str, action_id: str, body: ActionBody) -> dict[str, Any]:
        event = get_event(event_id)
        return plain(event_id, lambda: set_action(event, action_id, body.done))

    @app.post("/api/events/{event_id}/problems/{disruption_id}/decision")
    async def decision(event_id: str, disruption_id: str, body: DecisionBody) -> dict[str, Any]:
        event = get_event(event_id)
        return plain(event_id, lambda: record_decision(event, disruption_id, body.kind, body.option, body.note))

    @app.post("/api/events/{event_id}/problems/{disruption_id}/relax")
    async def relax(event_id: str, disruption_id: str, body: RelaxBody) -> dict[str, Any]:
        event = get_event(event_id)
        entry = plain(event_id, lambda: relax_limits(event, disruption_id, body.budget_eur, body.capacity_slack))
        return {"scored": entry["scored"], "relaxed": entry["relaxed"]}

    @app.post("/api/events/{event_id}/problems/{disruption_id}/replan")
    async def replan(event_id: str, disruption_id: str, body: ReplanBody, request: Request) -> dict[str, Any]:
        """Relax a limit that needs new options (e.g. another date): the Replanner runs again."""
        event = get_event(event_id)
        entry = await counted(request, event_id, "other", lambda: guarded(
            event_id, lambda: replan_with(event, disruption_id, body.note, runner_for(event_id))))
        return {"scored": entry["scored"], "relaxed": entry["relaxed"]}

    @app.put("/api/events/{event_id}/outbox/{message_id}")
    async def edit(event_id: str, message_id: str, body: EditBody) -> dict[str, Any]:
        event = get_event(event_id)
        return plain(event_id, lambda: edit_message(event, message_id, body.subject, body.body))

    @app.post("/api/events/{event_id}/whatif")
    async def whatif(event_id: str, body: WhatIfBody) -> dict[str, Any]:
        """Dry run in code: no agent runs, nothing is saved."""
        event = get_event(event_id)
        try:
            out = simulate(event.data, body.budget_eur, body.attendees, body.delay_node, body.delay_minutes, body.problem)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err
        for a in out["apply"]:   # Replay can only apply a problem that was recorded
            a["replayable"] = (a["kind"] != "problem" or not is_replay(event_id)
                               or (recordings_for(event_id) / slugify(a["text"])).is_dir())
        return out

    @app.post("/api/events/{event_id}/budget")
    async def budget(event_id: str, body: BudgetBody) -> dict[str, Any]:
        """Apply a budget change for real (code only), re-scoring open problems."""
        event = get_event(event_id)
        plain(event_id, lambda: set_budget(event, body.remaining_eur))
        return state(event)

    @app.post("/api/events/{event_id}/outbox/approve")
    async def approve(event_id: str, body: ApproveBody) -> dict[str, Any]:
        event = get_event(event_id)
        try:
            approved = approve_messages(event, body.ids)
        except OrchestratorError as err:
            raise HTTPException(400, str(err)) from err
        broker.publish(event_id, "approved", {"ids": [m["id"] for m in approved]})
        return {"approved": approved}

    @app.post("/api/events/{event_id}/analyze")
    async def analyze(event_id: str, request: Request, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        """Event files -> Impact Analyst -> event model (Live: uploaded to Cominty; Replay: recorded setup)."""
        event = get_event(event_id)
        max_bytes = int(visitors.limits.max_file_mb * 1024 * 1024) if is_visitor_event(event_id) else SETUP_MAX_BYTES
        data = []
        for f in files:
            blob = await f.read(max_bytes + 1)   # read at most one byte past the limit
            data.append((Path(f.filename or "file").name, blob))
        try:   # odd files are refused before any agent runs (and before a try is counted)
            check_setup_files(data, max_bytes)
        except OrchestratorError as err:
            raise HTTPException(400, str(err)) from err
        result = await counted(request, event_id, "analyses", lambda: guarded(
            event_id, lambda: analyze_event(event, data, runner_for(event_id), max_bytes), "not-event|" + NOT_AN_EVENT))
        broker.clear(event_id)
        return {**result, "state": state(get_event(event_id))}

    @app.post("/api/events/{event_id}/setup/confirm")
    async def confirm(event_id: str) -> dict[str, Any]:
        event = get_event(event_id)
        confirm_setup(event)
        return state(event)

    @app.get("/api/demo-files")
    async def demo_files() -> list[dict[str, Any]]:
        return [{"name": n, "bytes": (DATA / n).stat().st_size} for n in DEMO_FILES if (DATA / n).exists()]

    @app.get("/api/demo-files/{name}")
    async def demo_file(name: str) -> FileResponse:
        if name not in DEMO_FILES or not (DATA / name).is_file():
            raise HTTPException(404, f"no demo file {name!r}")
        return FileResponse(DATA / name, media_type=DEMO_TYPES[Path(name).suffix], filename=name)

    @app.get("/api/events/{event_id}/venues")
    async def venues(event_id: str, disruption: Optional[str] = None) -> dict[str, Any]:
        """Map pins for the lost venue and the Finder's candidates (only real addresses are located)."""
        event = get_event(event_id)
        history = event.data["history"]
        entry = (next((h for h in history if h["id"] == disruption), None) if disruption
                 else next((h for h in reversed(history) if h.get("candidates")), None))
        if entry is None:
            return {"disruption": disruption, "lost": None, "candidates": []}
        return await venue_pins(entry, event.data["model"]["nodes"], geo)

    @app.get("/api/events/{event_id}/files/{name}")
    async def download(event_id: str, name: str) -> FileResponse:
        event = get_event(event_id)
        path = event.files_dir / name
        if not re.fullmatch(r"[\w.-]+", name) or path.suffix not in FILE_TYPES or not path.is_file():
            raise HTTPException(404, f"no file {name!r}")
        return FileResponse(path, media_type=FILE_TYPES[path.suffix], filename=name)

    @app.get("/api/events/{event_id}/stream")
    async def stream(event_id: str, request: Request, follow: bool = True) -> StreamingResponse:
        """Sends everything logged so far, then (follow=true) live messages until the client leaves."""
        async def gen() -> AsyncIterator[str]:
            q = broker.subscribe(event_id)
            try:
                for msg in list(broker.log.get(event_id, [])):
                    yield sse(msg)
                while follow:
                    if await request.is_disconnected():
                        break
                    try:
                        yield sse(await asyncio.wait_for(q.get(), timeout=15))
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"   # keeps proxies from closing the connection
            finally:
                broker.unsubscribe(event_id, q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


app = create_app()
