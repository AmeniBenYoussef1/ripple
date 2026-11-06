"""Deterministic impact propagation and agent routing. No LLM calls here.

1. `propagate(model, disruption)`  -> waves of affected nodes (BFS by edge type)
2. `route(impact, ...)`            -> which agents to wake, which to skip, which to serve from cache

Propagation rules depend on the disruption kind: a delay travels along time edges
(scheduled_after, depends_on, must_inform) but never to the venue; a lost venue
travels along every edge it touches. Capacity is checked in code: if a group grows
but the venue still fits, the venue is NOT affected and the Resource Finder sleeps.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable

from .models import (
    Disruption, DisruptionKind, EdgeType, EventModel, Node, NodeType,
)

# Which edge types a disruption of each kind travels along.
FOLLOW: dict[DisruptionKind, set[EdgeType]] = {
    DisruptionKind.lost: {EdgeType.located_at, EdgeType.depends_on, EdgeType.scheduled_after,
                          EdgeType.must_inform, EdgeType.constrained_by},
    DisruptionKind.supplier_failed: {EdgeType.depends_on, EdgeType.scheduled_after,
                                     EdgeType.must_inform, EdgeType.constrained_by},
    DisruptionKind.delayed: {EdgeType.scheduled_after, EdgeType.depends_on, EdgeType.must_inform},
    DisruptionKind.resized: {EdgeType.sized_by, EdgeType.must_inform},
    DisruptionKind.cost_changed: {EdgeType.constrained_by, EdgeType.must_inform},
}

# What happens to a reached node, by disruption kind and node type.
EFFECT: dict[DisruptionKind, dict[NodeType, str]] = {
    DisruptionKind.lost: {NodeType.commitment: "moved", NodeType.schedule_item: "moved",
                          NodeType.group: "inform", NodeType.resource: "lost",
                          NodeType.constraint: "rescore"},
    DisruptionKind.supplier_failed: {NodeType.commitment: "moved", NodeType.schedule_item: "moved",
                                     NodeType.group: "inform", NodeType.resource: "lost",
                                     NodeType.constraint: "rescore"},
    DisruptionKind.delayed: {NodeType.commitment: "moved", NodeType.schedule_item: "moved",
                             NodeType.group: "inform", NodeType.resource: "check",
                             NodeType.constraint: "rescore"},
    DisruptionKind.resized: {NodeType.commitment: "resize", NodeType.schedule_item: "check",
                             NodeType.group: "inform", NodeType.resource: "insufficient",
                             NodeType.constraint: "rescore"},
    DisruptionKind.cost_changed: {NodeType.commitment: "check", NodeType.schedule_item: "check",
                                  NodeType.group: "inform", NodeType.resource: "check",
                                  NodeType.constraint: "rescore"},
}

ORIGIN_EFFECT = {
    DisruptionKind.lost: "lost",
    DisruptionKind.supplier_failed: "lost",
    DisruptionKind.delayed: "moved",
    DisruptionKind.resized: "resized",
    DisruptionKind.cost_changed: "rescore",
}

AGENTS = ("impact_analyst", "resource_finder", "replanner", "coordinator")


@dataclass
class Impact:
    origin: str
    kind: DisruptionKind
    waves: list[list[str]]              # waves[0] = nodes directly reached from the origin
    effects: dict[str, str]             # node id -> effect (origin included)
    checked_ok: list[str] = field(default_factory=list)  # reached but passed a code check (e.g. capacity)

    @property
    def affected(self) -> list[str]:
        return [n for w in self.waves for n in w]

    def to_dict(self) -> dict:
        return {"origin": self.origin, "kind": self.kind.value, "waves": self.waves,
                "effects": self.effects, "checked_ok": self.checked_ok}


def _capacity_ok(resource: Node, new_size: int | None) -> bool:
    cap = resource.attrs.get("capacity")
    if cap is None or new_size is None:
        return False  # unknown -> treat as affected so a human/agent looks at it
    return int(cap) >= int(new_size)


def propagate(model: EventModel, d: Disruption) -> Impact:
    nodes = {n.id: n for n in model.nodes}
    if d.node not in nodes:
        raise KeyError(f"disruption references unknown node {d.node!r}")
    allowed = FOLLOW[d.kind]
    out: dict[str, list[tuple[str, EdgeType]]] = {n: [] for n in nodes}
    for e in model.edges:
        if e.from_id in nodes and e.to_id in nodes:
            out[e.from_id].append((e.to_id, e.type))

    effects = {d.node: ORIGIN_EFFECT[d.kind]}
    seen = {d.node}
    waves: list[list[str]] = []
    checked_ok: list[str] = []
    frontier = [d.node]
    new_size = d.params.get("new_size")

    while frontier:
        nxt: list[str] = []
        for cur in frontier:
            # groups only receive information; they do not pass the change on
            if nodes[cur].type == NodeType.group and cur != d.node:
                continue
            for target, etype in out[cur]:
                if etype not in allowed or target in seen:
                    continue
                tnode = nodes[target]
                # code check: a grown group only affects a resource that no longer fits
                if d.kind == DisruptionKind.resized and tnode.type == NodeType.resource:
                    seen.add(target)
                    if _capacity_ok(tnode, new_size):
                        checked_ok.append(target)
                        continue
                seen.add(target)
                effects[target] = EFFECT[d.kind][tnode.type]
                nxt.append(target)
        if nxt:
            waves.append(nxt)
        frontier = nxt

    return Impact(origin=d.node, kind=d.kind, waves=waves, effects=effects, checked_ok=checked_ok)


# ---------------------------------------------------------------------------
# routing

def input_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class RouteDecision:
    run: list[str]
    skipped: dict[str, str]        # agent -> reason
    cached: dict[str, str]         # agent -> input hash reused
    slices: dict[str, list[str]]   # agent -> node ids it receives
    hashes: dict[str, str] = field(default_factory=dict)  # agent -> input hash (store after a successful run)

    def to_dict(self) -> dict:
        return {"run": self.run, "skipped": self.skipped, "cached": self.cached,
                "slices": self.slices, "hashes": self.hashes}


def _neighbours(model: EventModel, ids: Iterable[str]) -> set[str]:
    ids = set(ids)
    out = set(ids)
    for e in model.edges:
        if e.from_id in ids:
            out.add(e.to_id)
        if e.to_id in ids:
            out.add(e.from_id)
    return out


def route(model: EventModel, impact: Impact, cache: dict[str, str] | None = None,
          analyst_already_ran: bool = True) -> RouteDecision:
    """Decide which agents run.

    The Impact Analyst runs before routing (it turns the organizer's message into the
    Disruption), so by default it is reported as already run, not re-woken.
    `cache` maps agent -> input hash of its last run; identical input = reuse output.
    """
    cache = cache or {}
    nodes = {n.id: n for n in model.nodes}
    eff = impact.effects

    finder_nodes = [n for n, e in eff.items() if nodes[n].type == NodeType.resource and e in ("lost", "insufficient")]
    replan_nodes = [n for n, e in eff.items()
                    if nodes[n].type in (NodeType.schedule_item, NodeType.commitment) and e == "moved"]
    coord_nodes = [n for n, e in eff.items()
                   if nodes[n].type == NodeType.group or e in ("resize", "inform")]

    wanted = {
        "resource_finder": (finder_nodes, "no resource lost or too small"),
        "replanner": (replan_nodes, "no schedule item or commitment moves"),
        "coordinator": (coord_nodes, "nobody needs to be informed"),
    }

    run: list[str] = []
    skipped: dict[str, str] = {}
    cached: dict[str, str] = {}
    slices: dict[str, list[str]] = {}
    hashes: dict[str, str] = {}
    if not analyst_already_ran:
        run.append("impact_analyst")
    for agent, (owned, reason) in wanted.items():
        if not owned:
            skipped[agent] = reason
            continue
        sl = sorted(_neighbours(model, owned))
        payload = {"disruption": impact.to_dict(),
                   "nodes": [nodes[i].model_dump(mode="json") for i in sl]}
        h = input_hash(payload)
        if cache.get(agent) == h:
            cached[agent] = h
            continue
        run.append(agent)
        slices[agent] = sl
        hashes[agent] = h
    return RouteDecision(run=run, skipped=skipped, cached=cached, slices=slices, hashes=hashes)
