"""One option = one room = one capacity, everywhere.

A venue page may list several rooms ("three amphitheatres seating 220, 516 and 854"). The Replanner
and the Resource Finder may then quote different numbers for the same option. Code settles it:
the option uses the smallest room that fits the attendees, and says "room to confirm" when the
venue has more than one size. The plan card, the venue list, the map, spare seats and the
what-if all read this one number.
"""
from __future__ import annotations

import re
from typing import Any, Optional

SEAT_WORDS = r"(?:seats?|seated|seating|places|people|persons|personnes|pers\b|guests)"
NUM_LIST = r"\d{2,4}(?:\s*(?:,|and|or|&|/)\s*\d{2,4})*"
# "220, 516 and 854 seats", "500-seat", "277 places"
AFTER = re.compile(rf"({NUM_LIST})\s*-?\s*{SEAT_WORDS}", re.I)
# "seating 220, 516 and 854", "capacity of 300", "seats 120"
BEFORE = re.compile(rf"(?:seating|seats|capacity(?:\s+of)?|up to)\s+({NUM_LIST})", re.I)


def room_sizes(*texts: Optional[str]) -> set[int]:
    """Every room size a text mentions (numbers next to a seat word)."""
    sizes: set[int] = set()
    for t in texts:
        for m in list(AFTER.finditer(t or "")) + list(BEFORE.finditer(t or "")):
            sizes.update(int(n) for n in re.findall(r"\d{2,4}", m.group(1)))
    return {s for s in sizes if 10 <= s <= 20000}


def core(name: str) -> str:
    return re.split(r"[(–-]", name)[0].strip().lower()


def match_candidate(name: str, candidates: list[dict]) -> Optional[int]:
    n = core(name)
    for i, c in enumerate(candidates):
        cn = core(c.get("name", ""))
        if cn and (cn in n or n in cn):
            return i
    return None


def settle_rooms(scenarios: list[dict], candidates: list[dict], attendees: Optional[int]) -> list[dict]:
    """Give each venue option one room: the smallest that fits, and a "venue" summary for the UI."""
    out = []
    for s in scenarios:
        s = dict(s)
        target = next((ch.get("replace_with") for ch in s.get("changes", []) if ch.get("replace_with")), None)
        if not target:
            out.append(s)
            continue
        i = match_candidate(target, candidates)
        cand = candidates[i] if i is not None else {}
        sizes = room_sizes(target, cand.get("fit_notes"))
        for v in (s.get("capacity"), cand.get("capacity")):
            if isinstance(v, int) and v > 0:
                sizes.add(v)
        need = s.get("attendees") or attendees
        fitting = sorted(x for x in sizes if need and x >= need)
        capacity = fitting[0] if fitting else (max(sizes) if sizes else s.get("capacity"))
        s["capacity"] = capacity
        s["venue"] = {
            "name": cand.get("name") or target,
            "url": cand.get("url"),
            "candidate": i,
            "capacity": capacity,
            "rooms": sorted(sizes),
            "room_to_confirm": len(sizes) > 1,
        }
        out.append(s)
    return out
