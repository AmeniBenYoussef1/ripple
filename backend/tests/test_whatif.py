"""What-if: a dry run in code. No agent runs, nothing is saved."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.orchestrator import FIXTURES, GENERATED_MODEL
from app.whatif import simulate, verdict

T1 = "The hall just cancelled."


@pytest.fixture
def client(tmp_path):
    app = create_app(events_dir=tmp_path / "events", fixtures_dir=FIXTURES, model_path=GENERATED_MODEL,
                     demo=True, replay_delay=(0, 0))
    with TestClient(app) as c:
        c.post("/api/events/demo/setup")
        c.post("/api/events/demo/disruptions", json={"text": T1})
        yield c, tmp_path


def test_preview_changes_nothing(client):
    c, tmp = client
    before = (tmp / "events" / "demo.json").read_text()
    stream_before = c.get("/api/events/demo/stream?follow=false").text
    out = c.post("/api/events/demo/whatif", json={"attendees": 600, "budget_eur": 100,
                                                  "delay_node": "workshop_s2", "delay_minutes": 120}).json()
    assert out["label"] == "Preview — no agents run, nothing changes"
    assert (tmp / "events" / "demo.json").read_text() == before          # nothing saved
    assert c.get("/api/events/demo/stream?follow=false").text == stream_before   # no agent event at all
    kinds_after = c.get("/api/events/demo").json()["costs"]
    assert len(kinds_after) == 3                                            # no new agent cost


def test_more_attendees_than_any_hall(client):
    c, _ = client
    out = c.post("/api/events/demo/whatif", json={"attendees": 600}).json()
    assert out["options"]["detail"]          # the analyst's one-line summary, for a short title
    rows = out["options"]["rows"]
    assert {r["now"]["verdict"] for r in rows} <= {"best", "ok"}
    assert {r["whatif"]["verdict"] for r in rows} == {"too small"}         # 277, 500, 516 seats < 600
    rip = out["attendees"]["ripple"]
    assert out["attendees"] == {"now": 200, "whatif": 600, "ripple": rip}
    assert "resource_finder" in rip["run"]                                  # the hall (220) is too small
    assert {"id": "venue_main_hall", "label": "Main hall", "effect": "too small"} in rip["parts"]
    assert out["apply"] == [{"kind": "problem", "text": "400 late registrations.", "replayable": False}]


def test_thirty_more_is_the_recorded_problem(client):
    c, _ = client
    out = c.post("/api/events/demo/whatif", json={"attendees": 230}).json()
    assert out["apply"][0] == {"kind": "problem", "text": "30 late registrations.", "replayable": True}


def test_delay_wakes_only_the_replanner_and_coordinator(client):
    c, _ = client
    out = c.post("/api/events/demo/whatif", json={"delay_node": "workshop_s2", "delay_minutes": 120}).json()
    rip = out["delay"]["ripple"]
    assert {g["id"] for g in rip["groups"]} == {"grp_workshop_participants", "grp_speaker_2"}
    assert rip["run"] == ["replanner", "coordinator"] and "resource_finder" in rip["skipped"]
    assert out["apply"][0]["text"] == "Workshop by Speaker 2 is 2 hours late." and out["apply"][0]["replayable"] is False


def test_over_budget_with_known_costs():
    data = {"model": json.loads(GENERATED_MODEL.read_text(encoding="utf-8")), "cache": {}, "history": [{
        "id": "d1", "text": "x", "coordinator": "waiting_for_choice",
        "scenarios": [{"id": "A", "title": "Cheap", "extra_cost_eur": 1000, "capacity": 250, "attendees": 200},
                      {"id": "B", "title": "Dear", "extra_cost_eur": 5000, "capacity": 300, "attendees": 200}]}]}
    frozen = copy.deepcopy(data)
    out = simulate(data, budget_eur=2000)
    by = {r["id"]: r for r in out["options"]["rows"]}
    assert by["A"]["whatif"]["verdict"] == "best" and by["B"]["whatif"]["verdict"] == "over budget"
    assert by["B"]["now"]["verdict"] == "ok"                                # 6000 left today
    assert out["apply"] == [{"kind": "budget", "value": 2000, "text": "Set the budget left to EUR 2000"}]
    assert data == frozen                                                   # pure function


def test_bad_inputs(client):
    c, _ = client
    assert c.post("/api/events/demo/whatif", json={"budget_eur": -1}).status_code == 400
    assert c.post("/api/events/demo/whatif", json={"delay_node": "grp_attendees", "delay_minutes": 30}).status_code == 400
    assert c.post("/api/events/demo/whatif", json={"attendees": 0}).status_code == 400


def test_apply_budget_rescores_open_problems_and_logs(client):
    c, _ = client
    s = c.post("/api/events/demo/budget", json={"remaining_eur": 4500}).json()
    budget = next(n for n in s["model"]["nodes"] if n["id"] == "budget")
    assert budget["attrs"]["remaining_eur"] == 4500
    assert s["log"][-1]["kind"] == "constraint" and "4500" in s["log"][-1]["text"]
    assert verdict({"feasible": False, "rank": None, "violations": ["too small: 200 people on site, 160 seats"]}) == "too small"
