"""How many people will be in the venue: the number the capacity check uses.

On site = the attendees (the attendee group's size, so a resize counts)
        + everyone in a people file (a CSV with a "role" column): speakers, mentors, jury,
          volunteers, organizers.
Not counted: suppliers (caterer, vendor...), who deliver but don't need a seat, and anyone the
file says joins remotely. A person listed in both files is counted once, as an attendee.
Everything is read from the event files, in code.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SUPPLIER = re.compile(r"\b(supplier|caterer|catering|vendor|sponsor|contractor|delivery|driver)\b", re.I)
REMOTE = re.compile(r"\b(remote|remotely|online|virtual|by video)\b", re.I)
TALKS = re.compile(r"\b(speaker|keynote|mentor|jury|judge|host|panelist|coach)", re.I)
VOLUNTEER = re.compile(r"\bvolunteer", re.I)
NOT_STAFF = {"participants.csv", "late_registrations.csv"}


@dataclass
class OnSite:
    total: Optional[int]
    parts: list[dict] = field(default_factory=list)    # [{"label": "attendees", "count": 150}, ...]

    def to_dict(self) -> dict:
        return {"total": self.total, "parts": self.parts}

    def text(self) -> str:
        return ", ".join(f"{p['count']} {p['label']}" for p in self.parts if p["count"])


def _rows(path: Path) -> list[dict[str, str]]:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


TALK_WORDS = {"speaker": "speakers", "keynote": "speakers", "mentor": "mentors", "coach": "mentors",
              "jury": "jury", "judge": "jury", "host": "hosts", "panelist": "panelists"}
TALK_ORDER = ["speakers", "mentors", "jury", "hosts", "panelists"]


def _role_kind(role: str) -> str:
    if TALKS.search(role):
        return "talks"
    if VOLUNTEER.search(role):
        return "volunteers"
    return "organizers and staff"


def _talk_label(words: list[str]) -> str:
    """The roles actually in the file: "speakers", "speakers and jury", "speakers, mentors and jury"."""
    names = sorted(set(words), key=TALK_ORDER.index) or ["speakers"]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def staff_on_site(data_dir: Optional[Path]) -> dict[str, int]:
    """People from the people files who will be in the venue, by kind."""
    if not data_dir or not data_dir.exists():
        return {}
    attendees = {r.get("email", "").lower() for r in _rows(data_dir / "participants.csv")} - {""}
    seen: set[str] = set()
    kinds: dict[str, int] = {}
    talks: list[str] = []
    for path in sorted(data_dir.glob("*.csv")):
        if path.name in NOT_STAFF:
            continue
        for r in _rows(path):
            role = (r.get("role") or "").strip()
            if not role or SUPPLIER.search(role):
                continue
            notes = " ".join(str(v) for k, v in r.items() if k and k.lower() in ("notes", "responsibility", "attendance"))
            if REMOTE.search(notes):
                continue
            key = (r.get("email") or "").lower() or "|".join(str(v) for v in r.values())
            if key in seen or key in attendees:
                continue
            seen.add(key)
            kind = _role_kind(role)
            kinds[kind] = kinds.get(kind, 0) + 1
            if kind == "talks":
                talks.append(TALK_WORDS[TALKS.search(role).group(1).lower()])
    if "talks" in kinds:
        kinds = {(_talk_label(talks) if k == "talks" else k): v for k, v in kinds.items()}
    return kinds


def on_site(attendees: Optional[int], data_dir: Optional[Path]) -> OnSite:
    if attendees is None:
        return OnSite(total=None)
    staff = staff_on_site(data_dir)
    rank = {"volunteers": 1, "organizers and staff": 2}          # speakers & co first, then volunteers, then staff
    parts = [{"label": "attendees", "count": attendees}] + [
        {"label": k, "count": n} for k, n in sorted(staff.items(), key=lambda kv: rank.get(kv[0], 0))]
    return OnSite(total=attendees + sum(staff.values()), parts=parts)
