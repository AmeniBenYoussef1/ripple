"""Ripple HTTP API (FastAPI) on top of the orchestrator.

Run:  cd backend && .venv/Scripts/uvicorn app.main:app --reload --port 8000
With DEMO_MODE=true in .env, agent replies are replayed from fixtures/ (no Cominty calls).

Live progress goes to GET /api/events/{id}/stream as Server-Sent Events:
  started, agent (ToolCall / LlmStep / Replay with a description), cost, impact (waves),
  routing (run / skipped / cached), candidates, scenarios, saved, outbox, questions, done, error.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agents import AgentOutputError
from .geo import Geocoder, venue_pins
from .orchestrator import (DATA, EVENTS, FIXTURES, GENERATED_MODEL, SETUP_MAX_BYTES, Event, OrchestratorError,
                           Runner, analyze_event, approve_messages, choose_scenario, confirm_setup, demo_mode,
                           handle_disruption, load_event, new_event, total_cost)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DEFAULT_AREA = os.environ.get("RIPPLE_SEARCH_AREA", "Paris, France")
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


class DisruptionBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


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
               geocoder: Optional[Geocoder] = None) -> FastAPI:
    demo = demo_mode() if demo is None else demo
    replay_delay = replay_delay_from_env() if replay_delay is None else replay_delay

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if demo:
            app.state.client = None
            yield
            return
        from cominty_sdk import AsyncCominty
        async with AsyncCominty() as client:
            app.state.client = client
            yield

    app = FastAPI(title="Ripple API", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                       allow_methods=["*"], allow_headers=["*"])
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
        return Runner(client=app.state.client, demo=demo, fixtures_dir=fixtures_dir,
                      replay_delay=replay_delay if demo else (0.0, 0.0),
                      on_event=lambda e: broker.publish(event_id, "agent", e),
                      on_stage=lambda name, payload: broker.publish(event_id, name, payload))

    def state(event: Event) -> dict[str, Any]:
        saved = sum((Decimal(h.get("saved_total", "0")) for h in event.data["history"]), Decimal(0))
        files = sorted(p.name for p in event.files_dir.glob("*")) if event.files_dir.exists() else []
        return {**event.data, "demo_mode": demo, "total_cost": str(total_cost(event)),
                "saved_total": str(saved), "files": files}

    async def guarded(event_id: str, work):
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

    @app.post("/api/events/{event_id}/setup")
    async def setup(event_id: str, body: Optional[SetupBody] = None) -> dict[str, Any]:
        area = (body.search_area if body and body.search_area else None) or DEFAULT_AREA
        try:
            event = new_event(event_id, model_path=model_path, events_dir=events_dir, search_area=area)
        except OrchestratorError as err:
            raise HTTPException(400, str(err)) from err
        broker.clear(event_id)
        return state(event)

    @app.post("/api/events/{event_id}/reset")
    async def reset(event_id: str) -> dict[str, Any]:
        area = get_event(event_id).data.get("search_area") or DEFAULT_AREA
        event = new_event(event_id, model_path=model_path, events_dir=events_dir, search_area=area)
        broker.clear(event_id)
        broker.publish(event_id, "reset", {})
        return state(event)

    @app.get("/api/events/{event_id}")
    async def get_state(event_id: str) -> dict[str, Any]:
        return state(get_event(event_id))

    @app.post("/api/events/{event_id}/disruptions")
    async def disruption(event_id: str, body: DisruptionBody) -> dict[str, Any]:
        event = get_event(event_id)
        return await guarded(event_id, lambda: handle_disruption(event, body.text.strip(), runner_for(event_id)))

    @app.post("/api/events/{event_id}/scenarios/{scenario_id}/choose")
    async def choose(event_id: str, scenario_id: str) -> dict[str, Any]:
        event = get_event(event_id)
        return await guarded(event_id, lambda: choose_scenario(event, scenario_id, runner_for(event_id)))

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
    async def analyze(event_id: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        """Event files -> Impact Analyst -> event model (Live: uploaded to Cominty; Replay: recorded setup)."""
        event = get_event(event_id)
        data = []
        for f in files:
            blob = await f.read(SETUP_MAX_BYTES + 1)   # read at most one byte past the limit
            data.append((Path(f.filename or "file").name, blob))
        result = await guarded(event_id, lambda: analyze_event(event, data, runner_for(event_id)))
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
