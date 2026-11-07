"""Ripple data model, shared by agents (JSON contracts), the impact engine and the API.

Edge direction: `from -> to` means "a change to `from` can affect `to`".
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeType(str, Enum):
    resource = "resource"            # venue, room, Wi-Fi, bus, equipment
    commitment = "commitment"        # catering delivery, AV setup (time + place + supplier)
    schedule_item = "schedule_item"  # talks, rounds, lunch, workshop
    group = "group"                  # attendees, speakers, volunteers, a supplier contact
    constraint = "constraint"        # budget cap, accessibility, max travel time


class EdgeType(str, Enum):
    located_at = "located_at"            # venue -> things happening there
    scheduled_after = "scheduled_after"  # earlier item -> later item
    depends_on = "depends_on"            # prerequisite -> dependent
    must_inform = "must_inform"          # thing -> group that must know about it
    sized_by = "sized_by"                # group -> resource/commitment whose size depends on it
    constrained_by = "constrained_by"    # constraint -> what it limits


class DisruptionKind(str, Enum):
    lost = "lost"                        # a resource is gone (venue cancelled)
    supplier_failed = "supplier_failed"  # a commitment's supplier fails
    delayed = "delayed"                  # a schedule item or commitment moves in time
    resized = "resized"                  # a group's size changes
    cost_changed = "cost_changed"        # a constraint (budget) changes


class Node(BaseModel):
    id: str
    type: NodeType
    label: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    source: Optional[str] = None  # file the fact came from, e.g. "catering_contract.pdf"


class Edge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    type: EdgeType


# Words an agent may use instead of a node type, or as the name of a group of nodes
# ("nodes": {"resources": [...], "program": [...]}). Matched on the singular, lower case.
TYPE_WORDS = {
    "resource": "resource", "venue": "resource", "room": "resource", "space": "resource",
    "equipment": "resource", "facility": "resource", "location": "resource", "place": "resource",
    "commitment": "commitment", "delivery": "commitment", "contract": "commitment", "booking": "commitment",
    "order": "commitment", "setup": "commitment", "task": "commitment", "logistic": "commitment",
    "schedule_item": "schedule_item", "schedule": "schedule_item", "program": "schedule_item",
    "programme": "schedule_item", "session": "schedule_item", "agenda": "schedule_item", "talk": "schedule_item",
    "workshop": "schedule_item", "meal": "schedule_item", "activity": "schedule_item", "item": "schedule_item",
    "group": "group", "people": "group", "audience": "group", "person": "group", "participant": "group",
    "attendee": "group", "stakeholder": "group",
    "constraint": "constraint", "budget": "constraint", "limit": "constraint", "rule": "constraint",
}
NODE_FIELDS = {"id", "type", "label", "attrs", "source"}


def _type_word(word: Any) -> Optional[str]:
    if not isinstance(word, str) or not word.strip():
        return None
    w = word.strip().lower().replace("-", "_").replace(" ", "_")
    for cand in (w, w[:-1] if w.endswith("s") else w, w.split("_")[0]):
        if cand in TYPE_WORDS:
            return TYPE_WORDS[cand]
    return None


def normalize_nodes(nodes: Any) -> Any:
    """Accept the same facts in a slightly different shape, in code, before validation:
    nodes grouped by kind become one list, a word like "venue" becomes the type "resource",
    and fields outside "attrs" (start, room, ...) move into attrs instead of being dropped."""
    if isinstance(nodes, dict):                      # {"resources": [...], "groups": [...]}
        flat = []
        for bucket, items in nodes.items():
            for n in items if isinstance(items, list) else []:
                if isinstance(n, dict):
                    flat.append({"_bucket": bucket, **n})
        nodes = flat
    if not isinstance(nodes, list):
        return nodes
    out = []
    for n in nodes:
        if not isinstance(n, dict):
            out.append(n)
            continue
        n = dict(n)
        bucket = n.pop("_bucket", None)
        attrs = dict(n.get("attrs") or {}) if isinstance(n.get("attrs"), dict) else {}
        given = n.get("type")
        kind = n.get("kind")
        if given not in NodeType.__members__:
            # the agent's own word ("venue", "budget") is kept as attrs.kind
            n["type"] = _type_word(given) or _type_word(kind) or _type_word(bucket) or given
            sub = given if isinstance(given, str) and given != n["type"] else None
            if sub and "kind" not in attrs:
                attrs["kind"] = sub
        for k in [k for k in n if k not in NODE_FIELDS]:
            v = n.pop(k)
            if k == "kind" and _type_word(v) == n.get("type"):
                continue                             # "kind": "group" only repeated the type
            attrs.setdefault(k, v)
        src = n.get("source")
        if isinstance(src, list):                    # several files: keep all, the one with contacts first
            files = [x for x in src if isinstance(x, str)]
            attrs.setdefault("sources", files)
            order = (".csv", ".pdf", ".txt", ".json")
            rank = lambda f: next((i for i, e in enumerate(order) if f.lower().endswith(e)), len(order))
            n["source"] = min(files, key=rank) if files else None
        if not n.get("label") and isinstance(n.get("id"), str):
            n["label"] = n["id"].replace("_", " ").strip().capitalize()
        n["attrs"] = attrs
        out.append(n)
    return out


class EventModel(BaseModel):
    event_id: str
    title: str = ""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_shapes(cls, data: Any) -> Any:
        if isinstance(data, dict) and "nodes" in data:
            data = {**data, "nodes": normalize_nodes(data["nodes"])}
        return data

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)


class Disruption(BaseModel):
    node: str
    kind: DisruptionKind
    detail: str = ""
    params: dict[str, Any] = Field(default_factory=dict)  # e.g. {"delay_minutes": 120} or {"new_size": 230}


# ---- Scenarios (Replanner output, scored by code) --------------------------

class GroupBurden(BaseModel):
    group: str
    people: int
    minutes: int = 0             # time lost or extra travel per person (agent estimate)
    must_replan: bool = False    # e.g. postponement: everyone reorganizes their day


class Scenario(BaseModel):
    id: str
    title: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    capacity: Optional[int] = None
    attendees: Optional[int] = None
    extra_cost_eur: Optional[float] = None   # None = unknown, still to quote
    accessibility_ok: bool = True
    within_opening_hours: bool = True
    burdens: list[GroupBurden] = Field(default_factory=list)
    to_confirm: list[str] = Field(default_factory=list)
    on_site: Optional[dict[str, Any]] = None  # set in code: {"total", "parts"} (see headcount.py)


# ---- Resource Finder and Coordinator outputs (see skills/ripple-contracts/reference.md) ----

class Candidate(BaseModel):
    name: str
    url: str                                 # every web fact needs a source
    area: str = ""
    capacity: Optional[int] = None           # None when the page doesn't say
    est_cost_eur: Optional[float] = None
    fit_notes: str = ""
    availability: str = "to confirm"         # never claimed by an agent
    address: Optional[str] = None            # street address, only if the source page states it
    lat: Optional[float] = None              # coordinates, only if the source page states them
    lon: Optional[float] = None


class Action(BaseModel):
    """One step of the recovery plan, for the organizer's checklist (Coordinator output)."""
    title: str
    description: str = ""
    owner: str = ""                          # a role or team from the files, e.g. "Volunteer lead"
    deadline: str = ""                       # e.g. "07:30" or "before Friday"; taken from the plan
    priority: str = "medium"                 # high | medium | low


class OutboxMessage(BaseModel):
    group: str
    to: str
    channel: str = "email"
    subject: str = ""
    body: str
    status: str = "draft"                    # stays draft until the organizer approves
