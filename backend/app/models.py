"""Ripple data model, shared by agents (JSON contracts), the impact engine and the API.

Edge direction: `from -> to` means "a change to `from` can affect `to`".
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


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


class EventModel(BaseModel):
    event_id: str
    title: str = ""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

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


class OutboxMessage(BaseModel):
    group: str
    to: str
    channel: str = "email"
    subject: str = ""
    body: str
    status: str = "draft"                    # stays draft until the organizer approves
