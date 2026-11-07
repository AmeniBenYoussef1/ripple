"""Who a message goes to, resolved in code from the event files (never typed by an agent).

Rules, by the group's source file:
  participants.csv      everyone, or workshop=yes for a workshop group
  late_registrations    the newcomers of a late registration (group "grp_newcomers")
  people.csv            a person whose name is the group label ("Speaker 2"),
                        else everyone whose role matches the label ("Speakers" -> speaker)
  a text or PDF file    the email address on the line that names the group (e.g. the caterer in a contract)
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from .exports import NEWCOMERS_GROUP, participant_filter
from .models import EventModel

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def full_name(r: dict[str, str]) -> str:
    return (r.get("name") or f"{r.get('first_name', '')} {r.get('last_name', '')}").strip()


def _person(r: dict[str, str]) -> dict[str, str]:
    return {"name": full_name(r), "email": r.get("email", "")}


def text_lines(path: Path) -> list[str]:
    """Lines of a text or PDF file (a contract usually names the supplier and its email on one line)."""
    if path.suffix == ".pdf":
        from pypdf import PdfReader
        return [line for page in PdfReader(path).pages for line in (page.extract_text() or "").splitlines()]
    return path.read_text(encoding="utf-8").splitlines()


def member_groups(model: EventModel, group_id: str) -> list[str]:
    """Sub-groups of a group, from the same file: "grp_speakers" -> "grp_speaker_1", "grp_speaker_2"..."""
    node = next((n for n in model.nodes if n.id == group_id), None)
    if node is None or node.type.value != "group":
        return []
    prefix = node.id.rstrip("s") + "_"
    return [n.id for n in model.nodes
            if n.type.value == "group" and n.id != node.id and n.id.startswith(prefix) and n.source == node.source]


def one_message_per_person(messages: list[dict[str, Any]], label=lambda g: g) -> list[dict[str, Any]]:
    """Nobody gets two messages: people in several messages get one merged message with everything
    that changed for them (the most specific message first, e.g. Speaker 2's own before the Speakers one)."""
    first: dict[str, int] = {}
    person: dict[str, dict] = {}
    keys: dict[str, list[int]] = {}
    for i, m in enumerate(messages):
        for p in m.get("recipients") or []:
            e = p["email"].lower()
            person.setdefault(e, p)
            keys.setdefault(e, []).append(i)
    buckets: dict[tuple, list[dict]] = {}
    for e, idx in keys.items():
        buckets.setdefault(tuple(idx), []).append(person[e])
    out: list[tuple[int, dict]] = []
    for key, people in buckets.items():
        parts = sorted(key, key=lambda i: (len(messages[i].get("recipients") or []), i))
        base = dict(messages[parts[0]])
        if len(parts) > 1:
            base["body"] = base.get("body", "") + "".join(
                f"\n\nAlso for you, as one of the {label(messages[o]['group'])}:\n{messages[o].get('body', '')}"
                for o in parts[1:])
            base["also_groups"] = list(dict.fromkeys(messages[o]["group"] for o in parts[1:]
                                                     if messages[o]["group"] != base["group"]))
        base["recipients"] = people
        out.append((min(key), address(base)))
    # messages nobody could be found for stay as they are (they say so)
    out += [(i, address(m)) for i, m in enumerate(messages) if not m.get("recipients")]
    return [m for _, m in sorted(out, key=lambda x: x[0])]


def resolve(model: EventModel, group_id: str, data_dir: Path) -> dict[str, Any]:
    """{"recipients": [{name, email}], "source": file name or None}."""
    if group_id == NEWCOMERS_GROUP:
        return {"recipients": [_person(r) for r in _rows(data_dir / "late_registrations.csv")],
                "source": "late_registrations.csv"}
    node = next((n for n in model.nodes if n.id == group_id), None)
    if node is None or not node.source:
        return {"recipients": [], "source": None}
    path = data_dir / node.source
    label = node.label.strip().lower()

    if node.source == "participants.csv":
        keep = participant_filter(node.label, node.id)
        return {"recipients": [_person(r) for r in _rows(path) if keep(r)], "source": node.source}

    if path.suffix == ".csv":
        rows = _rows(path)
        exact = [r for r in rows if full_name(r).lower() in (label, label.split("(")[0].strip())]
        if exact:
            return {"recipients": [_person(r) for r in exact], "source": node.source}
        # one person the analysis linked by email: use it only if that email is really in the file
        email = str(node.attrs.get("email", "")).lower()
        if email:
            found = [r for r in rows if r.get("email", "").lower() == email]
            if found:
                return {"recipients": [_person(r) for r in found], "source": node.source}
        # a role group: any word of the label names a role ("Speakers & mentors" -> speaker, mentor)
        words = {w.rstrip("s") for w in re.findall(r"[a-z]+", label) if len(w) > 3}
        people = [_person(r) for r in rows
                  if words & {w.rstrip("s") for w in re.findall(r"[a-z]+", r.get("role", "").lower())}]
        # "Speakers" (3) with a mentor and a jury lead among them: add the people of its
        # sub-groups (grp_speaker_s1, grp_speaker_s2...), resolved the same way
        size = node.attrs.get("size")
        if isinstance(size, int) and len(people) < size:
            for child in member_groups(model, node.id):
                if True:
                    for p in resolve(model, child, data_dir)["recipients"]:
                        if p["email"] not in {x["email"] for x in people}:
                            people.append(p)
        return {"recipients": people, "source": node.source}

    if path.suffix in (".txt", ".pdf") and path.exists():
        for line in text_lines(path):
            if label in line.lower() or any(w in line.lower() for w in label.split() if len(w) > 4):
                emails = EMAIL.findall(line)
                if emails:
                    return {"recipients": [{"name": node.label, "email": emails[0]}], "source": node.source}
    return {"recipients": [], "source": node.source}


def address(message: dict[str, Any]) -> dict[str, Any]:
    """Fill "to" from the resolved recipients; keep the agent's words as a hint only."""
    people = message.get("recipients") or []
    if len(people) == 1:
        to = people[0]["email"]
    elif people:
        to = f"{len(people)} recipients"
    else:
        to = "No recipient found in your files"
    return {**message, "to": to, "recipient_count": len(people)}
