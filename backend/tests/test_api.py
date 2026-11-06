"""API tests in DEMO_MODE: the recorded fixtures are replayed, nothing calls Cominty."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.orchestrator import FIXTURES, GENERATED_MODEL

T1, T2, T3 = "The hall just cancelled.", "Speaker 2's train is 2 hours late.", "30 late registrations."


@pytest.fixture
def client(tmp_path):
    app = create_app(events_dir=tmp_path / "events", fixtures_dir=FIXTURES, model_path=GENERATED_MODEL, demo=True,
                     replay_delay=(0, 0))
    with TestClient(app) as c:
        assert c.post("/api/events/demo/setup", json={"search_area": "Paris, France"}).status_code == 200
        yield c


def sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_health_and_cors(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.json() == {"ok": True, "demo_mode": True}
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.get("/api/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in other.headers


def test_setup_gives_a_fresh_state(client):
    s = client.get("/api/events/demo").json()
    assert s["history"] == [] and s["search_area"] == "Paris, France" and s["demo_mode"] is True
    assert any(n["id"] == "venue_main_hall" for n in s["model"]["nodes"])


def test_full_demo_over_http(client):
    one = client.post("/api/events/demo/disruptions", json={"text": T1}).json()
    assert one["routing"]["run"] == ["resource_finder", "replanner", "coordinator"]
    assert one["coordinator"] == "waiting_for_choice"
    top = next(s for s in one["scored"] if s["feasible"])

    chosen = client.post(f"/api/events/demo/scenarios/{top['id']}/choose").json()
    assert chosen["outbox"] and {m["status"] for m in chosen["outbox"]} == {"draft"}

    ids = [m["id"] for m in chosen["outbox"][:2]]
    approved = client.post("/api/events/demo/outbox/approve", json={"ids": ids}).json()["approved"]
    assert [m["status"] for m in approved] == ["approved", "approved"]

    two = client.post("/api/events/demo/disruptions", json={"text": T2}).json()
    assert "resource_finder" in two["routing"]["skipped"]
    three = client.post("/api/events/demo/disruptions", json={"text": T3}).json()
    assert three["routing"]["run"] == ["coordinator"] and three["coordinator"] == "done"

    s = client.get("/api/events/demo").json()
    assert len(s["history"]) == 3
    assert float(s["total_cost"]) > 0 and float(s["saved_total"]) > 0
    assert "d1-revised_schedule.ics" in s["files"] and "d1-attendee_update.csv" in s["files"]


def test_file_download(client):
    one = client.post("/api/events/demo/disruptions", json={"text": T1}).json()
    client.post(f"/api/events/demo/scenarios/{one['scored'][0]['id']}/choose")
    ics = client.get("/api/events/demo/files/d1-revised_schedule.ics")
    assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
    assert ics.text.startswith("BEGIN:VCALENDAR\r\n") and "T080000" in ics.text
    csv = client.get("/api/events/demo/files/d1-attendee_update.csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0] == "name,email,team,groups,what_changed"
    assert client.get("/api/events/demo/files/..%2F..%2F.env").status_code == 404
    assert client.get("/api/events/demo/files/nothing.ics").status_code == 404


def test_stream_replays_the_progress_log(client):
    client.post("/api/events/demo/disruptions", json={"text": T2})
    r = client.get("/api/events/demo/stream?follow=false")
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "started" and kinds[-1] == "done"
    for k in ("agent", "cost", "impact", "routing", "scenarios", "saved"):
        assert k in kinds
    impact = dict(events)["impact"]
    assert impact["waves"] == [["grp_workshop_participants", "grp_speaker_2"]]
    routing = dict(events)["routing"]
    assert routing["run"] == ["replanner", "coordinator"] and "resource_finder" in routing["skipped"]
    agent_events = [d for k, d in events if k == "agent"]
    assert all(d["replay"] for d in agent_events)
    assert any(d["event"] == "ToolCall" and d["description"] for d in agent_events)   # the recorded live steps
    assert {d["agent"] for d in agent_events} == {"impact", "replanner"}


def test_reset_clears_history_and_stream(client):
    client.post("/api/events/demo/disruptions", json={"text": T2})
    s = client.post("/api/events/demo/reset").json()
    assert s["history"] == [] and s["costs"] == []
    kinds = [k for k, _ in sse_events(client.get("/api/events/demo/stream?follow=false").text)]
    assert kinds == ["reset"]


def test_replayable_options_and_answer_endpoint(client):
    one = client.post("/api/events/demo/disruptions", json={"text": T1}).json()
    s = client.get("/api/events/demo").json()
    rec = s["replayable"][one["id"]]["options"]
    assert one["scored"][0]["id"] in rec                 # the top option is recorded
    missing = [x["id"] for x in one["scored"] if x["id"] not in rec]
    if missing:
        r = client.post(f"/api/events/demo/scenarios/{missing[0]}/choose")
        assert r.status_code == 400 and "no recording" in r.json()["detail"]
    r = client.post("/api/events/demo/answer", json={"text": "Room B"})
    assert r.status_code == 400 and "no question" in r.json()["detail"]


def test_errors_are_clear(client):
    assert client.get("/api/events/nope").status_code == 404
    r = client.post("/api/events/demo/disruptions", json={"text": "The caterer went bankrupt."})
    assert r.status_code == 400 and "no recording" in r.json()["detail"]      # DEMO_MODE, not recorded
    assert client.post("/api/events/demo/disruptions", json={"text": ""}).status_code == 422
    assert client.post("/api/events/demo/scenarios/A/choose").status_code == 400  # nothing ready
    assert client.post("/api/events/demo/outbox/approve", json={"ids": ["x"]}).status_code == 400
    assert client.post("/api/events/bad%20id/setup").status_code == 400


def test_venues_endpoint_uses_the_geocoder(tmp_path):
    from app.geo import Geocoder

    async def fetch(q):
        return (48.87, 2.34)
    app = create_app(events_dir=tmp_path / "events", fixtures_dir=FIXTURES, model_path=GENERATED_MODEL,
                     demo=True, replay_delay=(0, 0), geocoder=Geocoder(tmp_path / "geo.json", fetch))
    with TestClient(app) as c:
        c.post("/api/events/demo/setup")
        assert c.get("/api/events/demo/venues").json()["candidates"] == []
        c.post("/api/events/demo/disruptions", json={"text": T1})
        out = c.get("/api/events/demo/venues").json()
        assert out["lost"]["lat"] is None                         # the demo hall's address is fictional
        assert len(out["candidates"]) >= 3
        assert all(v["url"].startswith("http") for v in out["candidates"])
        with_address = [v for v in out["candidates"] if v["address"]]
        assert all(v["lat"] is not None for v in with_address)
