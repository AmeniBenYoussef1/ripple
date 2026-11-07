"""Files built in code (not by an agent): revised_schedule.ics and attendee_update.csv.

The Coordinator only writes message texts and a revised schedule as JSON. Building the
files here makes them always valid and always match the real participant list.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .models import EventModel, NodeType

# ---- revised_schedule.ics -----------------------------------------------------------


def to_hhmmss(value: Any) -> Optional[str]:
    """'9:00', '09:00' or '09:00:00' -> '090000'. None if it is not a time."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", str(value or ""))
    if not m:
        return None
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if h > 23 or mi > 59 or s > 59:
        return None
    return f"{h:02d}{mi:02d}{s:02d}"


def _escape(text: str) -> str:
    """iCalendar TEXT escaping (RFC 5545, 3.3.11)."""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space (RFC 5545, 3.1)."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def build_ics(items: list[dict[str, Any]], model: EventModel, date: str,
              now: Optional[datetime] = None) -> str:
    """One VEVENT per revised item. Times are local ('floating'), as written in the schedule.

    Items look like {"id", "start", "end", "place"}; a missing start/end/title is taken
    from the event model node with the same id. Items without a usable start are skipped.
    """
    day = date.replace("-", "")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    nodes = {n.id: n for n in model.nodes}
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Ripple//Event update//EN", "CALSCALE:GREGORIAN"]
    for it in items:
        node = nodes.get(it.get("id"))
        attrs = node.attrs if node else {}
        start = to_hhmmss(it.get("start") or it.get("time") or attrs.get("start") or attrs.get("time"))
        if start is None:
            continue
        end = to_hhmmss(it.get("end") or attrs.get("end"))
        if end is None or end <= start:   # no end given: 30 minutes
            t = datetime.strptime(start, "%H%M%S") + timedelta(minutes=30)
            end = t.strftime("%H%M%S")
        title = it.get("title") or (node.label if node else it.get("id", "Event item"))
        lines += ["BEGIN:VEVENT",
                  f"UID:{it.get('id', 'item')}-{day}@ripple.example.com",
                  f"DTSTAMP:{stamp}",
                  f"DTSTART:{day}T{start}",
                  f"DTEND:{day}T{end}",
                  f"SUMMARY:{_escape(title)}"]
        if it.get("place"):
            lines.append(f"LOCATION:{_escape(it['place'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


NEWCOMERS_GROUP = "grp_newcomers"   # people in late_registrations.csv (not a node of the model)

# ---- attendee_update.csv --------------------------------------------------------------


def participant_filter(group_label: str, group_id: str):
    """Which participants.csv rows belong to a group: workshop groups = workshop 'yes', others = everyone."""
    if "workshop" in f"{group_label} {group_id}".lower():
        return lambda row: row.get("workshop", "").strip().lower() == "yes"
    return lambda row: True


def _read_rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_attendee_csv(participants_csv: Path, model: EventModel, messages: list[dict[str, Any]],
                       newcomers_csv: Optional[Path] = None) -> tuple[str, int]:
    """One row per participant who gets at least one message, with every update for them.

    Only groups built from participants.csv count (plus the newcomers of a late registration,
    from `newcomers_csv`); speakers, volunteers and the caterer get their own direct messages.
    Returns (csv text, number of people).
    """
    groups = {n.id: n for n in model.nodes if n.type == NodeType.group and n.source == "participants.csv"}
    rows = _read_rows(participants_csv)
    newcomers = _read_rows(newcomers_csv) if newcomers_csv and newcomers_csv.exists() else []

    updates: dict[str, dict[str, Any]] = {}   # email -> row being built (keeps file order below)
    for m in messages:
        if m.get("group") == NEWCOMERS_GROUP:
            label, members = "Newcomers", newcomers
        elif m.get("group") in groups:
            g = groups[m["group"]]
            keep = participant_filter(g.label, g.id)
            label, members = g.label, [r for r in rows if keep(r)]
        else:
            continue
        for r in members:
            name = (r.get("name") or f"{r.get('first_name', '')} {r.get('last_name', '')}").strip()
            u = updates.setdefault(r["email"], {"name": name, "email": r["email"],
                                                "team": r.get("team", ""), "groups": [], "what_changed": []})
            u["groups"].append(label)
            u["what_changed"].append(m.get("subject") or "Update")

    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["name", "email", "team", "groups", "what_changed"])
    order = [r["email"] for r in rows + newcomers if r["email"] in updates]
    for email in order:
        u = updates[email]
        w.writerow([u["name"], u["email"], u["team"], "; ".join(u["groups"]), " | ".join(u["what_changed"])])
    return out.getvalue(), len(order)
