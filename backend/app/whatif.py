"""What-if simulator: a dry run of the code (score, propagate, route). No agent, nothing saved.

The organizer changes the budget, the number of attendees, or delays one item. Ripple shows,
next to today's situation: how the options of the latest open problem would rank (and which
would be over budget or too small), who would be affected, and which agents would wake.
"""
from __future__ import annotations

from typing import Any, Optional

from .impact_engine import propagate, route
from .models import Disruption, EventModel, NodeType, Scenario
from .scoring import score


def attendee_group(model: EventModel) -> Optional[str]:
    """The group whose size drives the venue and the catering (it has sized_by edges)."""
    sized = {e.from_id for e in model.edges if e.type.value == "sized_by"}
    groups = [n for n in model.nodes if n.type == NodeType.group and n.id in sized]
    return max(groups, key=lambda n: n.attrs.get("size") or 0).id if groups else None


def budget_node(model: EventModel):
    return next((n for n in model.nodes if n.type == NodeType.constraint
                 and isinstance(n.attrs.get("remaining_eur"), (int, float))), None)


def verdict(s: dict) -> str:
    """One plain word for an option: best / ok / over budget / too small / not possible."""
    if s["feasible"]:
        return "best" if s["rank"] == 1 else "ok"
    v = " ".join(s["violations"])
    if "over remaining budget" in v:
        return "over budget"
    if "too small" in v or "capacity" in v:
        return "too small"
    return "not possible"


def _ripple(model: EventModel, d: Disruption, cache: dict[str, str]) -> dict[str, Any]:
    nodes = {n.id: n for n in model.nodes}
    impact = propagate(model, d)
    decision = route(model, impact, cache)
    groups = [{"id": g, "label": nodes[g].label, "size": nodes[g].attrs.get("size")}
              for g in impact.effects if nodes[g].type == NodeType.group and g != d.node]
    words = {"insufficient": "too small", "moved": "has to move", "resize": "new quantity", "lost": "lost", "check": "to check"}
    parts = [{"id": n, "label": nodes[n].label, "effect": words.get(e, e)}
             for n, e in impact.effects.items() if nodes[n].type != NodeType.group and n != d.node]
    return {"disruption": d.model_dump(mode="json"), "impact": impact.to_dict(), "groups": groups, "parts": parts,
            "run": decision.run, "skipped": decision.skipped, "cached": list(decision.cached)}


def _label_ok(text: str) -> str:
    return text.rstrip(".")


def simulate(event_data: dict[str, Any], budget_eur: Optional[float] = None, attendees: Optional[int] = None,
             delay_node: Optional[str] = None, delay_minutes: int = 0, problem_id: Optional[str] = None) -> dict[str, Any]:
    """Return today's situation and the what-if side by side. Pure: reads event_data, changes nothing."""
    model = EventModel.model_validate(event_data["model"])
    what = model.model_copy(deep=True)
    cache = {a: v["hash"] for a, v in event_data.get("cache", {}).items()}
    nodes = {n.id: n for n in what.nodes}
    out: dict[str, Any] = {"label": "Preview — no agents run, nothing changes", "apply": []}

    # budget
    b_now = budget_node(model)
    budget_now = b_now.attrs["remaining_eur"] if b_now else None
    if budget_eur is not None:
        if budget_eur < 0:
            raise ValueError("the budget cannot be negative")
        b = budget_node(what)
        if b is None:
            raise ValueError("this event has no budget")
        b.attrs["remaining_eur"] = budget_eur
        if budget_eur != budget_now:
            out["apply"].append({"kind": "budget", "value": budget_eur,
                                 "text": f"Set the budget left to EUR {budget_eur:g}"})
    out["budget"] = {"now": budget_now, "whatif": budget_eur if budget_eur is not None else budget_now}

    # attendees
    gid = attendee_group(model)
    size_now = model.node(gid).attrs.get("size") if gid else None
    out["attendees"] = {"now": size_now, "whatif": attendees if attendees is not None else size_now, "ripple": None}
    if attendees is not None and gid and attendees != size_now:
        if attendees <= 0:
            raise ValueError("the number of attendees must be positive")
        d = Disruption(node=gid, kind="resized", params={"new_size": attendees},
                       detail=f"{nodes[gid].label}: {size_now} -> {attendees}")
        out["attendees"]["ripple"] = _ripple(what, d, cache)
        delta = attendees - (size_now or 0)
        text = f"{delta} late registrations." if delta > 0 else f"{-delta} cancellations."
        out["apply"].append({"kind": "problem", "text": text})

    # delay
    out["delay"] = None
    if delay_node and delay_minutes:
        if delay_node not in nodes or nodes[delay_node].type not in (NodeType.schedule_item, NodeType.commitment):
            raise ValueError("pick a session or a commitment to delay")
        if delay_minutes < 0 or delay_minutes > 24 * 60:
            raise ValueError("a delay is between 1 minute and 24 hours")
        d = Disruption(node=delay_node, kind="delayed", params={"delay_minutes": delay_minutes},
                       detail=f"{nodes[delay_node].label} {delay_minutes} min late")
        out["delay"] = {"node": delay_node, "label": nodes[delay_node].label, "minutes": delay_minutes,
                        "ripple": _ripple(what, d, cache)}
        hours = f"{delay_minutes // 60} hours" if delay_minutes % 60 == 0 and delay_minutes >= 120 else \
            "1 hour" if delay_minutes == 60 else f"{delay_minutes} minutes"
        out["apply"].append({"kind": "problem", "text": f"{_label_ok(nodes[delay_node].label)} is {hours} late."})

    # options of an open problem (default: the latest one still waiting for a choice)
    history = event_data.get("history", [])
    entry = (next((h for h in history if h["id"] == problem_id), None) if problem_id else
             next((h for h in reversed(history) if h.get("scenarios")
                   and h.get("coordinator") == "waiting_for_choice"), None)
             or next((h for h in reversed(history) if h.get("scenarios")), None))
    out["options"] = None
    if entry is not None:
        slack = entry.get("relaxed", {}).get("capacity_slack", 0)
        scen = [Scenario.model_validate(s) for s in entry["scenarios"]]
        # everyone on site changes by the same number as the attendees (speakers and volunteers stay)
        grow = (attendees - size_now) if attendees and isinstance(size_now, int) else 0
        revised = [s.model_copy(update={"attendees": s.attendees + grow}) if grow and s.attendees is not None else s
                   for s in scen]
        b_what = budget_node(what)
        now = [x.to_dict() for x in score(scen, budget_now, slack)]
        later = [x.to_dict() for x in score(revised, b_what.attrs["remaining_eur"] if b_what else None, slack)]
        by_id = {x["id"]: x for x in later}
        out["options"] = {"problem": entry["id"], "text": entry["text"],
                          "detail": (entry.get("disruption") or {}).get("detail", ""), "rows": [
            {"id": x["id"], "title": x["title"],
             "now": {"rank": x["rank"], "verdict": verdict(x), "violations": x["violations"]},
             "whatif": {"rank": by_id[x["id"]]["rank"], "verdict": verdict(by_id[x["id"]]),
                        "violations": by_id[x["id"]]["violations"]}}
            for x in now]}
    return out
