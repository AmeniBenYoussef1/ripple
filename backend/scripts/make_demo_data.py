"""Generate Ripple's fictional demo files (deterministic: same output every run).

Run:  cd backend && .venv/Scripts/python scripts/make_demo_data.py
Writes participants.csv, people.csv, schedule.json, budget.json into demo_data/.
catering_contract.txt is hand-written and left untouched.
All people are fictional and use @example.com emails.
"""
import csv
import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "demo_data"

FIRST = ["Ada", "Ben", "Chloe", "Dario", "Elif", "Farid", "Greta", "Hugo", "Ines", "Jonas",
         "Kaya", "Liam", "Mira", "Nils", "Olga", "Pablo", "Quinn", "Rosa", "Sami", "Tara",
         "Umar", "Vera", "Wen", "Yara", "Zeno"]
LAST = ["Arden", "Brook", "Calder", "Dune", "Ember", "Fallow", "Glen", "Harbor", "Isle", "Juniper",
        "Kestrel", "Linden", "Moss", "North", "Orchard", "Pike", "Quarry", "Reed", "Sorrel", "Thorn"]

N_ATTENDEES = 200
N_WORKSHOP = 40
TEAM_SIZE = 4
# must match catering_contract.txt (200 boxes: 20 vegetarian, 8 vegan, 4 gluten-free)
DIETARY = ["vegetarian"] * 20 + ["vegan"] * 8 + ["gluten-free"] * 4


def unique_names(rng: random.Random, n: int) -> list[str]:
    pairs = [f"{f} {l}" for f in FIRST for l in LAST]  # 500 combinations
    rng.shuffle(pairs)
    return pairs[:n]


def email(name: str) -> str:
    return name.lower().replace(" ", ".") + "@example.com"


def write_participants(rng: random.Random, names: list[str]) -> None:
    dietary = DIETARY + ["none"] * (N_ATTENDEES - len(DIETARY))
    rng.shuffle(dietary)
    workshop = set(rng.sample(range(N_ATTENDEES), N_WORKSHOP))
    with open(OUT / "participants.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "email", "team", "dietary", "workshop"])
        for i, name in enumerate(names):
            team = f"team_{i // TEAM_SIZE + 1:02d}"
            w.writerow([name, email(name), team, dietary[i], "yes" if i in workshop else "no"])


def write_people(names: list[str]) -> None:
    rows = [
        ["Speaker 1", "speaker1@example.com", "speaker", "Opening keynote 09:00"],
        ["Speaker 2", "speaker2@example.com", "speaker", "Workshop 10:00-11:00, room B; arrives by train"],
        ["Speaker 3", "speaker3@example.com", "speaker", "Awards host 16:30"],
    ]
    for i, name in enumerate(names):
        role = "volunteer lead" if i == 0 else "volunteer"
        rows.append([name, email(name), role, "Meet-up 07:45 at the main hall"])
    with open(OUT / "people.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "email", "role", "notes"])
        w.writerows(rows)


def write_schedule() -> None:
    schedule = {
        "event": "CodeSprint 2026 (fictional demo event)",
        "date": "2026-11-21",
        "venue": {
            "name": "Main hall",
            "address": "[FICTIONAL ADDRESS]",
            "capacity": 220,
            "note": "Everything below takes place at the main hall unless a room is given.",
        },
        "commitments": [
            {"id": "volunteer_meetup", "title": "Volunteer meet-up", "time": "07:45",
             "location": "Main hall", "inform": ["volunteers"]},
            {"id": "av_setup", "title": "AV setup", "time": "08:00", "location": "Main hall",
             "needed_for": ["opening"]},
            {"id": "catering_delivery", "title": "Catering delivery", "time": "12:00",
             "location": "Main hall", "needed_for": ["lunch"], "details": "see catering_contract.txt"},
            {"id": "contest_problems", "title": "Contest problems", "details": "prepared by the organizers"},
            {"id": "online_judge", "title": "Online judge", "details": "hosted platform used for the rounds"},
            {"id": "prizes", "title": "Prizes", "details": "bought in advance"},
        ],
        "program": [
            {"id": "opening", "title": "Opening", "start": "09:00", "end": "09:30",
             "audience": ["attendees", "speakers"]},
            {"id": "round_1", "title": "Contest round 1", "start": "09:30", "end": "12:00", "audience": ["attendees"]},
            {"id": "workshop_s2", "title": "Workshop by Speaker 2", "start": "10:00", "end": "11:00",
             "location": "Main hall, room B", "audience": ["workshop participants (workshop=yes)", "Speaker 2"]},
            {"id": "lunch", "title": "Lunch", "start": "12:00", "end": "13:00", "audience": ["attendees"]},
            {"id": "round_2", "title": "Contest round 2", "start": "13:00", "end": "16:00", "audience": ["attendees"]},
            {"id": "awards", "title": "Awards", "start": "16:30", "end": "17:00", "audience": ["attendees"]},
        ],
    }
    (OUT / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n", encoding="utf-8")


def write_budget() -> None:
    lines = [
        {"item": "Main hall rental", "eur": 2400},
        {"item": "Catering (200 lunch boxes, see catering_contract.txt)", "eur": 1900},
        {"item": "AV equipment", "eur": 600},
        {"item": "Prizes", "eur": 900},
        {"item": "Online judge licence", "eur": 200},
    ]
    total = 7500
    spent = sum(l["eur"] for l in lines)
    budget = {"currency": "EUR", "total_eur": total, "committed": lines,
              "committed_eur": spent, "remaining_eur": total - spent}
    (OUT / "budget.json").write_text(json.dumps(budget, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    rng = random.Random(2026)
    names = unique_names(rng, N_ATTENDEES + 12)
    write_participants(rng, names[:N_ATTENDEES])
    write_people(names[N_ATTENDEES:])
    write_schedule()
    write_budget()
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
