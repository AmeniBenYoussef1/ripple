"""Recipients come from the event files (code), and drafts can be edited with history kept."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import EventModel
from app.orchestrator import DATA, FIXTURES, GENERATED_MODEL, OrchestratorError, new_event, edit_message
from app.recipients import address, resolve

MODEL = EventModel.model_validate(json.loads(GENERATED_MODEL.read_text(encoding="utf-8")))


def emails(group):
    return [p["email"] for p in resolve(MODEL, group, DATA)["recipients"]]


def test_each_group_resolves_from_its_file():
    assert len(emails("grp_attendees")) == 200
    assert len(emails("grp_workshop_participants")) == 40
    assert emails("grp_speakers") == ["speaker1@example.com", "speaker2@example.com", "speaker3@example.com"]
    assert emails("grp_speaker_2") == ["speaker2@example.com"]
    assert len(emails("grp_volunteers")) == 12
    assert emails("grp_caterer") == ["orders@greenfork.example.com"]     # from the contract, not the client line
    assert len(emails("grp_newcomers")) == 30
    assert resolve(MODEL, "grp_attendees", DATA)["source"] == "participants.csv"
    assert resolve(MODEL, "nobody", DATA) == {"recipients": [], "source": None}
    all_emails = [e for g in ("grp_attendees", "grp_volunteers", "grp_newcomers", "grp_caterer") for e in emails(g)]
    assert all(e.endswith("example.com") for e in all_emails)


def test_to_line_comes_from_the_recipients():
    one = address({"recipients": [{"name": "Speaker 2", "email": "speaker2@example.com"}]})
    assert one["to"] == "speaker2@example.com" and one["recipient_count"] == 1
    many = address({"recipients": [{"name": "a", "email": "a@example.com"}] * 40})
    assert many["to"] == "40 recipients"
    assert address({"recipients": []})["to"] == "No recipient found in your files"


@pytest.fixture
def client(tmp_path):
    app = create_app(events_dir=tmp_path / "events", fixtures_dir=FIXTURES, model_path=GENERATED_MODEL,
                     demo=True, replay_delay=(0, 0))
    with TestClient(app) as c:
        c.post("/api/events/demo/setup")
        yield c


def test_outbox_recipients_and_editing_over_http(client):
    one = client.post("/api/events/demo/disruptions", json={"text": "The hall just cancelled."}).json()
    out = client.post(f"/api/events/demo/scenarios/{one['scored'][0]['id']}/choose").json()
    by_group = {m["group"]: m for m in out["outbox"]}
    assert by_group["grp_attendees"]["recipient_count"] == 160          # the 40 workshop participants get one merged message
    assert by_group["grp_workshop_participants"]["also_groups"] == ["grp_attendees"]
    assert by_group["grp_workshop_participants"]["recipient_count"] == 40
    assert by_group["grp_caterer"]["to"] == "orders@greenfork.example.com"
    assert all(m["to_hint"] for m in out["outbox"])            # the agent's words, kept as a hint

    m = by_group["grp_caterer"]
    r = client.put(f"/api/events/demo/outbox/{m['id']}", json={"subject": "New address", "body": "Please deliver to..."})
    assert r.status_code == 200
    edited = r.json()
    assert edited["subject"] == "New address" and edited["edited"] is True
    assert edited["edits"][0]["subject"] == m["subject"]       # the earlier version is kept
    client.post("/api/events/demo/outbox/approve", json={"ids": [m["id"]]})
    r = client.put(f"/api/events/demo/outbox/{m['id']}", json={"subject": "x", "body": "y"})
    assert r.status_code == 400 and "only drafts" in r.json()["detail"]
    assert client.put("/api/events/demo/outbox/nope", json={"subject": "x", "body": "y"}).status_code == 400
    log = client.get("/api/events/demo").json()["log"]
    assert any(l["kind"] == "edit" for l in log) and any(l["kind"] == "approval" for l in log)


def test_unchanged_edit_keeps_no_history(tmp_path):
    ev = new_event("e", events_dir=tmp_path)
    ev.data["history"] = [{"id": "d1", "outbox": [{"id": "d1-m1", "group": "g", "status": "draft", "subject": "S", "body": "B"}]}]
    assert "edits" not in edit_message(ev, "d1-m1", "S", "B")
    with pytest.raises(OrchestratorError, match="subject and a text"):
        edit_message(ev, "d1-m1", "S", "  ")


def test_one_message_per_person_merges_the_most_specific_first():
    from app.recipients import one_message_per_person
    a = {"email": "a@example.com", "name": "A"}
    b = {"email": "b@example.com", "name": "B"}
    c = {"email": "c@example.com", "name": "C"}
    msgs = [
        {"group": "grp_speakers", "subject": "Venue change", "body": "New venue.", "recipients": [a, b, c]},
        {"group": "grp_speaker_2", "subject": "Your workshop moves", "body": "Room B.", "recipients": [b]},
        {"group": "grp_caterer", "subject": "Address", "body": "...", "recipients": []},
    ]
    out = one_message_per_person(msgs, {"grp_speakers": "Speakers"}.get)
    assert [(m["group"], m["recipient_count"]) for m in out] == [("grp_speakers", 2), ("grp_speaker_2", 1), ("grp_caterer", 0)]
    merged = out[1]
    assert merged["subject"] == "Your workshop moves" and merged["to"] == "b@example.com"
    assert merged["body"] == "Room B.\n\nAlso for you, as one of the Speakers:\nNew venue."
    emails = [p["email"] for m in out for p in m["recipients"]]
    assert sorted(emails) == ["a@example.com", "b@example.com", "c@example.com"]     # nobody twice


def test_members_of_an_affected_group_are_affected_one_wave_later():
    from app.impact_engine import propagate
    from app.models import Disruption
    from app.orchestrator import add_members
    imp = propagate(MODEL, Disruption(node="venue_main_hall", kind="lost"))
    assert "grp_speaker_1" not in imp.effects                              # the engine alone misses it
    add_members(MODEL, imp)
    assert imp.effects["grp_speaker_1"] == "inform"
    wave_of = {n: i for i, w in enumerate(imp.waves) for n in w}
    assert wave_of["grp_speaker_1"] == wave_of["grp_speakers"] + 1
