"""Capacity counts everyone on site, from the files."""
from app.headcount import on_site
from app.models import Scenario
from app.orchestrator import DATA
from app.scoring import score

PEOPLE = """name,email,role,notes
A,a@example.com,speaker,In Paris
B,b@example.com,mentor,Joins remotely by video
C,c@example.com,jury lead,
D,d@example.com,caterer,Delivers lunch
E,e@example.com,organizer,
Ana,ana@example.com,volunteer,
A again,a@example.com,speaker,
"""


def test_codesprint_counts_speakers_and_volunteers():
    here = on_site(200, DATA)
    assert here.total == 215
    assert here.parts == [{"label": "attendees", "count": 200},
                          {"label": "speakers", "count": 3},
                          {"label": "volunteers", "count": 12}]


def test_suppliers_remote_people_and_duplicates_are_not_counted(tmp_path):
    (tmp_path / "participants.csv").write_text("email\nana@example.com\nbo@example.com\n", encoding="utf-8")
    (tmp_path / "people.csv").write_text(PEOPLE, encoding="utf-8")
    # B joins remotely, D is a supplier, Ana is already an attendee, A is listed twice
    here = on_site(2, tmp_path)
    assert here.total == 5
    assert {p["label"]: p["count"] for p in here.parts} == {
        "attendees": 2, "speakers and jury": 2, "organizers and staff": 1}


def test_no_people_file_counts_attendees_only(tmp_path):
    assert on_site(150, tmp_path).total == 150 and on_site(None, tmp_path).total is None


def test_capacity_check_says_who_is_on_site():
    parts = [{"label": "attendees", "count": 150}, {"label": "speakers, mentors and jury", "count": 3},
             {"label": "volunteers", "count": 10}]
    small = Scenario(id="A", title="Rooftop", capacity=150, attendees=163, on_site={"total": 163, "parts": parts})
    big = Scenario(id="B", title="Hall", capacity=200, attendees=163, on_site={"total": 163, "parts": parts})
    b, a = score([small, big])
    assert a.violations == ["too small: 163 people on site, 150 seats"]
    assert b.spare == 37
    assert "163 people on site (150 attendees, 3 speakers, mentors and jury, 10 volunteers), 200 seats" in b.explanation
