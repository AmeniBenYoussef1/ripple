"""Orchestrator flow in DEMO_MODE: recordings are replayed, nothing calls Cominty."""
import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.orchestrator import (OrchestratorError, Runner, choose_scenario, handle_disruption,
                              load_event, new_event, slugify)

REFERENCE = Path(__file__).resolve().parents[1] / "demo_data" / "event_model.json"
T1, T2, T3 = "The hall just cancelled.", "Speaker 2's train is 2 hours late.", "30 late registrations."


def rec(data, cost):
    return {"agent": "x", "cost": cost, "raw": "", "data": data, "questions": [], "files": []}


SCENARIOS_1 = {"scenarios": [
    {"id": "A", "title": "Split across two small rooms", "capacity": 160, "attendees": 200,
     "burdens": [{"group": "grp_attendees", "people": 200, "minutes": 5}]},
    {"id": "B", "title": "Move to Hall C", "capacity": 250, "attendees": 200,
     "changes": [{"node": "venue_main_hall", "replace_with": "Hall C"}],
     "burdens": [{"group": "grp_attendees", "people": 200, "minutes": 15}], "to_confirm": ["Hall C availability"]},
    {"id": "C", "title": "Postpone one week", "extra_cost_eur": 0,
     "burdens": [{"group": "grp_attendees", "people": 200, "minutes": 0, "must_replan": True}]},
]}
OUTBOX = {"messages": [
    {"group": "grp_attendees", "to": "200 attendees", "subject": "New venue", "body": "We move to Hall C."},
    {"group": "grp_caterer", "to": "orders@greenfork.example.com", "subject": "New address", "body": "..."},
], "revised_schedule": [{"id": "lunch", "start": "12:30", "end": "13:30", "place": "Hall C"}]}
OUTBOX_3 = {"messages": [
    {"group": "grp_caterer", "to": "orders@greenfork.example.com", "subject": "230 lunch boxes", "body": "..."},
    {"group": "grp_newcomers", "to": "30 newcomers", "subject": "Welcome to CodeSprint", "body": "..."},
    {"group": "grp_attendees", "to": "200 attendees", "subject": "More people", "body": "should be dropped"},
]}

FIXTURES = {
    slugify(T1): {
        "impact_analyst": rec({"disruption": {"node": "venue_main_hall", "kind": "lost"}}, "0.01"),
        "resource_finder": rec({"candidates": [{"name": "Hall C", "url": "https://example.com/c", "capacity": 250}]}, "0.04"),
        "replanner": rec(SCENARIOS_1, "0.02"),
        "coordinator": rec(OUTBOX, "0.03"),
    },
    slugify(T2): {
        "impact_analyst": rec({"disruption": {"node": "workshop_s2", "kind": "delayed",
                                              "params": {"delay_minutes": 120}}}, "0.01"),
        "replanner": rec({"scenarios": [{"id": "A", "title": "Swap the workshop with lunch",
                                         "burdens": [{"group": "grp_workshop", "people": 40, "minutes": 10}]}]}, "0.02"),
    },
    slugify(T3): {
        "impact_analyst": rec({"disruption": {"node": "grp_attendees", "kind": "resized",
                                              "params": {"new_size": 230}}}, "0.01"),
        "coordinator": rec(OUTBOX_3, "0.02"),
    },
}


@pytest.fixture
def setup(tmp_path):
    fx = tmp_path / "fixtures"
    for slug, agents in FIXTURES.items():
        (fx / slug).mkdir(parents=True)
        for agent, data in agents.items():
            (fx / slug / f"{agent}.json").write_text(json.dumps(data))
    model = json.loads(REFERENCE.read_text())
    model["nodes"][0]["attrs"]["date"] = "2026-11-21"           # the reference model has no date
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(model))
    event = new_event("test", model_path=model_path, events_dir=tmp_path / "events")
    events = []
    runner = Runner(demo=True, fixtures_dir=fx, on_event=events.append)
    return event, runner, events, tmp_path


def run(coro):
    return asyncio.run(coro)


def test_hall_cancelled_runs_finder_then_replanner_and_scores(setup):
    event, runner, events, _ = setup
    out = run(handle_disruption(event, T1, runner))
    assert out["routing"]["run"] == ["resource_finder", "replanner", "coordinator"]
    assert out["impact"]["waves"][0] == ["av_setup", "catering_delivery", "volunteer_meetup", "workshop_s2"]
    assert out["candidates"][0]["name"] == "Hall C"
    ranked = [s["id"] for s in out["scored"]]
    assert ranked == ["B", "C", "A"]                     # A fails capacity, so it is last
    assert out["scored"][-1]["feasible"] is False
    assert out["coordinator"] == "waiting_for_choice"    # stops before the Coordinator
    assert out["costs"] == {"impact_analyst": "0.01", "resource_finder": "0.04", "replanner": "0.02"}
    assert [e["agent"] for e in events] == ["impact", "finder", "replanner"]


def test_choose_runs_coordinator_and_updates_model(setup):
    event, runner, _, _ = setup
    run(handle_disruption(event, T1, runner))
    out = run(choose_scenario(event, "B", runner))
    assert [m["group"] for m in out["outbox"]] == ["grp_attendees", "grp_caterer"]
    assert all(m["status"] == "draft" for m in out["outbox"])
    assert [m["id"] for m in out["outbox"]] == ["d1-the-hall-just-cancelled-m1", "d1-the-hall-just-cancelled-m2"]
    files = {f["name"]: f for f in out["files"]}
    assert files["d1-attendee_update.csv"]["rows"] == 200      # only attendees are in participants.csv
    ics = (event.files_dir / "d1-revised_schedule.ics").read_text()
    assert "DTSTART:20261121T123000" in ics
    venue = event.model.node("venue_main_hall")
    assert venue.attrs["capacity"] == 250 and venue.attrs["replaced_by"] == "Hall C"


def test_infeasible_scenario_cannot_be_chosen(setup):
    event, runner, _, _ = setup
    run(handle_disruption(event, T1, runner))
    with pytest.raises(OrchestratorError, match="hard limit"):
        run(choose_scenario(event, "A", runner))


def test_full_demo_sequence_skips_the_right_agents(setup):
    event, runner, _, _ = setup
    run(handle_disruption(event, T1, runner))
    run(choose_scenario(event, "B", runner))

    two = run(handle_disruption(event, T2, runner))
    assert two["routing"]["run"] == ["replanner", "coordinator"]
    assert "resource_finder" in two["routing"]["skipped"]
    assert two["impact"]["waves"] == [["grp_workshop", "grp_speaker_2"]]
    assert two["saved"]["resource_finder"] == {"cost": "0.04", "estimate": False,
                                               "basis": "average on this event"}

    three = run(handle_disruption(event, T3, runner))    # new hall holds 250 >= 230
    assert three["routing"]["run"] == ["coordinator"]
    assert set(three["routing"]["skipped"]) == {"resource_finder", "replanner"}
    assert three["impact"]["checked_ok"] == ["venue_main_hall"]
    assert Decimal(three["saved_total"]) == Decimal("0.04") + Decimal("0.02")
    assert three["coordinator"] == "done"                    # nothing to choose: informed at once
    assert [m["group"] for m in three["outbox"]] == ["grp_caterer", "grp_newcomers"]   # not existing attendees
    assert three["files"] == [{"name": "d3-attendee_update.csv", "kind": "csv", "rows": 30}]
    assert event.model.node("grp_attendees").attrs["size"] == 230


def test_same_disruption_twice_serves_replanner_from_cache(setup):
    event, runner, _, _ = setup
    run(handle_disruption(event, T2, runner))
    again = run(handle_disruption(event, T2, runner))
    assert "replanner" in again["routing"]["cached"]
    assert "replanner" not in again["costs"]
    assert again["scored"][0]["id"] == "A"               # cached scenarios are still scored


def test_event_is_stored_as_json(setup):
    event, runner, _, tmp = setup
    run(handle_disruption(event, T2, runner))
    stored = load_event("test", events_dir=tmp / "events")
    assert stored.data["history"][0]["disruption"]["node"] == "workshop_s2"
    assert {c["agent"] for c in stored.data["costs"]} == {"impact_analyst", "replanner"}
    assert "replanner" in stored.data["cache"]


def test_missing_recording_gives_clear_error(setup):
    event, runner, _, _ = setup
    with pytest.raises(OrchestratorError, match="no recording"):
        run(handle_disruption(event, "The caterer went bankrupt.", runner))


def test_node_checker_rejects_delay_on_a_group():
    from app.agents import DisruptionReply
    from app.models import EventModel
    from app.orchestrator import node_checker
    model = EventModel.model_validate(json.loads(REFERENCE.read_text()))
    check = node_checker(model)
    check(DisruptionReply.model_validate({"disruption": {"node": "workshop_s2", "kind": "delayed"}}))
    with pytest.raises(ValueError, match="session they lead"):
        check(DisruptionReply.model_validate({"disruption": {"node": "grp_speaker_2", "kind": "delayed"}}))
    with pytest.raises(ValueError, match="not in the event model"):
        check(DisruptionReply.model_validate({"disruption": {"node": "hall_x", "kind": "lost"}}))


# ---- the recorded live run (fixtures/), replayed against the generated model ----------

from app.orchestrator import FIXTURES as RECORDED, GENERATED_MODEL  # noqa: E402


@pytest.mark.skipif(not (RECORDED / slugify(T1) / "coordinator.json").exists(), reason="no recorded live run")
def test_recorded_live_run_replays_the_demo(tmp_path):
    event = new_event("replay", model_path=GENERATED_MODEL, events_dir=tmp_path)
    runner = Runner(demo=True, fixtures_dir=RECORDED)
    one = run(handle_disruption(event, T1, runner))
    assert one["routing"]["run"] == ["resource_finder", "replanner", "coordinator"]
    top = next(s for s in one["scored"] if s["feasible"])
    out = run(choose_scenario(event, top["id"], runner))
    assert out["outbox"] and all(m["status"] == "draft" for m in out["outbox"])
    assert {f["name"] for f in out["files"]} == {"d1-revised_schedule.ics", "d1-attendee_update.csv"}
    assert all((event.files_dir / f["name"]).exists() for f in out["files"])

    two = run(handle_disruption(event, T2, runner))
    assert two["disruption"]["node"] == "workshop_s2"
    assert two["routing"]["run"] == ["replanner", "coordinator"]

    three = run(handle_disruption(event, T3, runner))
    assert three["routing"]["run"] == ["coordinator"]
    assert three["coordinator"] == "done" and three["outbox"]
    assert three["impact"]["checked_ok"] == [one["disruption"]["node"]]   # the new hall still fits 230


def test_choosing_twice_is_refused(setup):
    event, runner, _, _ = setup
    run(handle_disruption(event, T1, runner))
    run(choose_scenario(event, "B", runner))
    with pytest.raises(OrchestratorError, match="already chosen"):
        run(choose_scenario(event, "C", runner))


def test_approve_marks_drafts_and_rejects_unknown_ids(setup):
    from app.orchestrator import approve_messages
    event, runner, _, _ = setup
    run(handle_disruption(event, T1, runner))
    out = run(choose_scenario(event, "B", runner))
    first = out["outbox"][0]["id"]
    with pytest.raises(OrchestratorError, match="unknown message ids"):
        approve_messages(event, [first, "nope"])
    assert all(m["status"] == "draft" for m in event.data["history"][0]["outbox"])   # nothing changed
    approved = approve_messages(event, [first])
    assert approved[0]["status"] == "approved"
    statuses = [m["status"] for m in event.data["history"][0]["outbox"]]
    assert statuses == ["approved", "draft"]


def test_stage_events_follow_the_flow(setup):
    event, runner, _, _ = setup
    run(handle_disruption(event, T1, runner))
    run(choose_scenario(event, "B", runner))                 # bigger hall, so only the Coordinator wakes
    stages = []
    runner.on_stage = lambda name, payload: stages.append(name)
    run(handle_disruption(event, T3, runner))
    assert stages[:4] == ["started", "cost", "impact", "routing"]
    assert "outbox" in stages and stages[-1] == "done"


def test_saved_estimate_falls_back_to_recordings(setup):
    event, runner, _, _ = setup
    two = run(handle_disruption(event, T2, runner))          # Finder never ran on this event
    assert two["saved"]["resource_finder"] == {"cost": "0.04", "estimate": True,
                                               "basis": "average of recorded runs"}
    assert two["saved_is_estimate"] is True


def test_replay_paces_recorded_steps(setup, monkeypatch):
    event, runner, events, tmp = setup
    rec_path = tmp / "fixtures" / slugify(T2) / "impact_analyst.json"
    data = json.loads(rec_path.read_text())
    data["events"] = [{"event": "ToolCall", "status": "running", "description": "read_file: Read schedule"},
                      {"event": "ToolCall", "status": "success", "description": "read_file: Read schedule"},
                      {"event": "Result", "status": "success", "description": ""}]
    rec_path.write_text(json.dumps(data))
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.orchestrator.asyncio.sleep", fake_sleep)
    runner.replay_delay = (0.3, 0.8)
    run(handle_disruption(event, T2, runner))
    impact_events = [e for e in events if e["agent"] == "impact"]
    assert [e["description"] for e in impact_events[:2]] == ["read_file: Read schedule"] * 2
    assert all(e["replay"] for e in impact_events)
    assert impact_events[-1]["event"] == "Replay"
    assert len(sleeps) >= 1 and all(0.3 <= x <= 0.8 for x in sleeps[:1])   # one pause per running step
