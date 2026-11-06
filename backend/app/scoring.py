"""Transparent scenario scoring. Agents propose scenarios; this code ranks them.

Order of decision (lexicographic, lower is better):
  0. hard constraints must all pass, otherwise the scenario is shown greyed out with reasons
  1. worst burden on any single group      (protect the group that loses most)
  2. total burden over all people          (people x minutes)
  3. extra cost in euros                   (unknown cost ranks after known costs)
  4. spare capacity, seats left over      (more room for late registrations; unknown ranks last)
  5. number of things still "to confirm"   (unverified availability is risk)

A group that must fully replan its day (e.g. postponement) counts as REPLAN_MINUTES per person.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import Scenario

REPLAN_MINUTES = 240  # assumption, shown in the UI: a full replan costs about half a day per person


@dataclass
class ScoredScenario:
    scenario: Scenario
    feasible: bool
    violations: list[str]
    worst_group: str | None
    worst_burden: int
    total_burden: int
    extra_cost: float | None
    unconfirmed: int
    rank: int | None = None
    explanation: list[str] = field(default_factory=list)
    tied_with: list[str] = field(default_factory=list)  # ids with exactly the same score key
    spare: int | None = None   # capacity - attendees, when both are known

    def key(self) -> tuple:
        cost = self.extra_cost if self.extra_cost is not None else math.inf
        spare = -self.spare if self.spare is not None else math.inf   # more spare seats rank first
        return (self.worst_burden, self.total_burden, cost, spare, self.unconfirmed)

    def to_dict(self) -> dict:
        return {
            "id": self.scenario.id, "title": self.scenario.title, "feasible": self.feasible,
            "violations": self.violations, "worst_group": self.worst_group,
            "worst_burden_min": self.worst_burden, "total_burden_person_min": self.total_burden,
            "extra_cost_eur": self.extra_cost, "unconfirmed": self.unconfirmed,
            "rank": self.rank, "explanation": self.explanation, "spare_seats": self.spare,
            "tied": bool(self.tied_with), "tied_with": self.tied_with,
        }


def hard_violations(s: Scenario, remaining_budget: float | None, capacity_slack: int = 0) -> list[str]:
    """capacity_slack: how many people the organizer accepts to place elsewhere (a relaxed limit)."""
    v = []
    if s.capacity is not None and s.attendees is not None and s.capacity < s.attendees - capacity_slack:
        v.append(f"capacity {s.capacity} for {s.attendees} people")
    if s.extra_cost_eur is not None and remaining_budget is not None and s.extra_cost_eur > remaining_budget:
        v.append(f"extra cost EUR {s.extra_cost_eur:g} over remaining budget EUR {remaining_budget:g}")
    if not s.accessibility_ok:
        v.append("accessibility requirement not met")
    if not s.within_opening_hours:
        v.append("schedule outside the venue's opening hours")
    return v


def burden_per_person(minutes: int, must_replan: bool) -> int:
    return max(minutes, REPLAN_MINUTES) if must_replan else minutes


def score(scenarios: list[Scenario], remaining_budget: float | None = None,
          capacity_slack: int = 0) -> list[ScoredScenario]:
    scored: list[ScoredScenario] = []
    for s in scenarios:
        viol = hard_violations(s, remaining_budget, capacity_slack)
        worst_group, worst, total = None, 0, 0
        for b in s.burdens:
            per = burden_per_person(b.minutes, b.must_replan)
            total += per * b.people
            if per > worst:
                worst, worst_group = per, b.group
        scored.append(ScoredScenario(
            scenario=s, feasible=not viol, violations=viol, worst_group=worst_group,
            worst_burden=worst, total_burden=total, extra_cost=s.extra_cost_eur,
            unconfirmed=len(s.to_confirm),
            spare=s.capacity - s.attendees if s.capacity is not None and s.attendees is not None else None,
        ))

    feasible = sorted((x for x in scored if x.feasible), key=lambda x: x.key())
    for i, x in enumerate(feasible, start=1):
        # identical keys share the best rank (1, 1, 3) and are marked as tied: the organizer decides
        x.tied_with = [y.scenario.id for y in feasible if y is not x and y.key() == x.key()]
        first_equal = next(j for j, y in enumerate(feasible, start=1) if y.key() == x.key())
        x.rank = first_equal
        x.explanation = [
            f"worst impact: {x.worst_group or 'nobody'}, {x.worst_burden} min per person",
            f"total impact: {x.total_burden} person-minutes",
            "extra cost: to quote" if x.extra_cost is None else f"extra cost: EUR {x.extra_cost:g}",
            f"spare seats: {x.spare}" if x.spare is not None else "spare seats: unknown",
            f"still to confirm: {x.unconfirmed}",
        ]
        if x.tied_with:
            x.explanation.append(f"tied with {', '.join(x.tied_with)}: same score on every criterion")
    for x in scored:
        if not x.feasible:
            x.explanation = ["fails a hard limit: " + "; ".join(x.violations)]
    return feasible + [x for x in scored if not x.feasible]
