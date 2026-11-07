"""Several events side by side: a new event never touches the CodeSprint demo or its recordings."""
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import EventModel, Node
from app.orchestrator import (FIXTURES, GENERATED_MODEL, Runner, analyze_event, load_event, new_event,
                              reset_problems)
from app.recipients import resolve

HERCODE = Path(__file__).resolve().parents[2] / "hercode_event"


@pytest.fixture
def app_and_dirs(tmp_path):
    app = create_app(events_dir=tmp_path / "events", fixtures_dir=FIXTURES, model_path=GENERATED_MODEL,
                     demo=True, replay_delay=(0, 0), recordings_dir=tmp_path / "recordings")
    with TestClient(app) as c:
        c.post("/api/events/demo/setup")
        yield c, tmp_path


def test_create_a_second_event_without_touching_the_demo(app_and_dirs):
    c, tmp = app_and_dirs
    before = (tmp / "events" / "demo.json").read_text()
    s = c.post("/api/events/hercode/create", json={"title": "HerCode Hackathon"}).json()
    assert s["event_id"] == "hercode" and s["model"]["nodes"] == [] and s["setup"]["status"] == "not_analyzed"
    assert c.post("/api/events/hercode/create").status_code == 409            # ids stay unique
    assert c.post("/api/events/demo/create").status_code == 400               # "demo" is reserved
    ids = {e["id"]: e for e in c.get("/api/events").json()}
    assert set(ids) == {"demo", "hercode"} and ids["hercode"]["title"] == "HerCode Hackathon"
    # Replay of a new event reads its own recordings folder, never CodeSprint's fixtures
    files = [("files", ("participants.csv", b"name,email\nA,a@example.com\n"))]
    r = c.post("/api/events/hercode/analyze", files=files)
    assert r.status_code == 400 and "no recording" in r.json()["detail"]
    assert (tmp / "events" / "demo.json").read_text() == before
    assert c.post("/api/events/hercode/setup").json()["model"]["nodes"] == []  # blank, not the CodeSprint model


def test_live_analyze_keeps_the_uploads_and_uses_the_event_id(tmp_path, monkeypatch):
    monkeypatch.setenv("COMINTY_AGENT_IMPACT", "agent-impact")
    model = EventModel(event_id="hercode", title="HerCode", nodes=[
        Node(id="grp_attendees", type="group", label="Participants", attrs={"size": 150}, source="participants.csv")])
    started = []

    class Result:   # named like the SDK event, so its cost is counted
        status, correlation_id = "success", 1
        data = SimpleNamespace(cost=SimpleNamespace(total=Decimal("0.01")), description=None, message=None, filename=None)

    class Run:
        thread = SimpleNamespace(id="t1")

        def __aiter__(self):
            async def gen():
                yield Result()
            return gen()

        async def result(self):
            return SimpleNamespace(content=json.dumps({"event_model": model.model_dump(by_alias=True)}),
                                   questions=[], files=[])

    async def upload_file(data, *, filename):
        return SimpleNamespace(id=filename)

    async def start(**kw):
        started.append(kw)
        return Run()

    chat = SimpleNamespace(upload_file=upload_file, start=start)
    rec = tmp_path / "recordings" / "hercode"
    ev = new_event("hercode", model_path=None, events_dir=tmp_path / "events")
    files = [(n, (HERCODE / n).read_bytes()) for n in ("participants.csv", "people.csv", "catering_contract.pdf")]
    asyncio.run(analyze_event(ev, files, Runner(client=SimpleNamespace(chat=chat), demo=False, fixtures_dir=rec)))
    assert 'event_id "hercode"' in started[0]["message"]
    assert sorted(p.name for p in ev.inputs_dir.iterdir()) == ["catering_contract.pdf", "participants.csv", "people.csv"]
    assert ev.data_dir == ev.inputs_dir
    assert (rec / "setup-live" / "setup_analyst.json").exists()
    assert not any(FIXTURES.glob("setup-live"))                               # nothing written to CodeSprint's
    # a reset keeps the analysis (and its cost), so retakes don't pay again
    ev.data["history"] = [{"id": "d1"}]
    reset_problems(ev)
    again = load_event("hercode", events_dir=tmp_path / "events")
    assert again.data["history"] == [] and again.model.nodes[0].id == "grp_attendees"
    assert again.data["costs"] == [{"disruption": "setup", "agent": "impact_analyst", "cost": "0.01"}]


@pytest.mark.skipif(not HERCODE.exists(), reason="hercode_event folder not present")
def test_recipients_from_the_hercode_files():
    model = EventModel(event_id="hercode", nodes=[
        Node(id="grp_participants", type="group", label="Participants", source="participants.csv"),
        Node(id="grp_workshop", type="group", label="Workshop participants", source="participants.csv"),
        Node(id="grp_lina", type="group", label="Lina Haddad", source="people.csv"),
        Node(id="grp_mentors", type="group", label="Speakers and mentors", source="people.csv"),
        Node(id="grp_volunteers", type="group", label="Volunteers", source="people.csv"),
        Node(id="grp_caterer", type="group", label="Saveurs & Co", source="catering_contract.pdf"),
    ])
    get = lambda g: resolve(model, g, HERCODE)["recipients"]
    assert len(get("grp_participants")) == 150 and len(get("grp_workshop")) == 35
    assert get("grp_participants")[0]["name"] == "Amira Chaabane"            # first_name + last_name
    assert [p["email"] for p in get("grp_lina")] == ["lina.haddad@example.com"]
    assert {p["email"] for p in get("grp_mentors")} == {"nadia.karim@example.com", "lina.haddad@example.com"}
    assert len(get("grp_volunteers")) == 10                                  # lead + 9 volunteers
    assert [p["email"] for p in get("grp_caterer")] == ["orders@saveurs-co.example.com"]   # read from the PDF
