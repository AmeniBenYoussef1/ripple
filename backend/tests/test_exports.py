"""revised_schedule.ics and attendee_update.csv are built in code: check they are valid."""
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from app.exports import build_attendee_csv, build_ics, to_hhmmss
from app.models import EventModel

DATA = Path(__file__).resolve().parents[1] / "demo_data"
MODEL = EventModel.model_validate(json.loads((DATA / "event_model.json").read_text()))
NOW = datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc)


def test_to_hhmmss():
    assert to_hhmmss("9:00") == "090000"
    assert to_hhmmss("12:30") == "123000"
    assert to_hhmmss("07:45:10") == "074510"
    assert to_hhmmss("noon") is None
    assert to_hhmmss("25:00") is None


def test_ics_is_valid_icalendar():
    items = [{"id": "lunch", "start": "12:30", "end": "13:30", "place": "Hall C, 2nd floor; east wing"},
             {"id": "av_setup", "start": "08:00"},                    # no end: 30 minutes
             {"id": "workshop_s2", "end": "13:00"},                   # no start: from the model (10:00)
             {"id": "not_a_time", "start": "later"}]                  # skipped
    ics = build_ics(items, MODEL, "2026-11-21", now=NOW)
    lines = ics.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR" and lines[-1] == ""
    assert "\n" not in ics.replace("\r\n", "")                       # CRLF only
    assert ics.count("BEGIN:VEVENT") == 3 == ics.count("END:VEVENT")
    assert "DTSTART:20261121T123000" in lines and "DTEND:20261121T133000" in lines
    assert "DTEND:20261121T083000" in lines
    assert "DTSTART:20261121T100000" in lines
    assert "DTSTAMP:20261106T200000Z" in lines
    assert "SUMMARY:Lunch" in lines
    assert "LOCATION:Hall C\\, 2nd floor\\; east wing" in lines        # RFC 5545 escaping
    assert all(len(line.encode()) <= 75 for line in lines)


def test_ics_folds_long_lines():
    ics = build_ics([{"id": "lunch", "start": "12:00", "place": "Ä" * 100}], MODEL, "2026-11-21", now=NOW)
    lines = ics.split("\r\n")
    assert all(len(line.encode()) <= 75 for line in lines)
    loc = [i for i, line in enumerate(lines) if line.startswith("LOCATION:")][0]
    assert lines[loc + 1].startswith(" ")                             # continuation line


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_csv_one_row_per_participant_with_all_their_updates():
    messages = [{"group": "grp_attendees", "subject": "New venue"},
                {"group": "grp_workshop", "subject": "Workshop room change"},
                {"group": "grp_volunteers", "subject": "Meet-up moved"},     # not in participants.csv
                {"group": "grp_caterer", "subject": "New address"}]
    text, n = build_attendee_csv(DATA / "participants.csv", MODEL, messages)
    r = rows(text)
    assert n == len(r) == 200                                        # not 255: no duplicates
    assert len({x["email"] for x in r}) == 200
    workshop = [x for x in r if "Workshop participants" in x["groups"]]
    assert len(workshop) == 40
    assert all(x["what_changed"] == "New venue | Workshop room change" for x in workshop)
    assert all(x["email"].endswith("@example.com") for x in r)


def test_csv_workshop_only():
    text, n = build_attendee_csv(DATA / "participants.csv", MODEL,
                                 [{"group": "grp_workshop", "subject": "Workshop moved to 12:00"}])
    assert n == 40
    assert {x["groups"] for x in rows(text)} == {"Workshop participants"}


def test_csv_empty_when_no_participant_group_is_messaged():
    text, n = build_attendee_csv(DATA / "participants.csv", MODEL, [{"group": "grp_caterer", "subject": "x"}])
    assert n == 0 and rows(text) == []
