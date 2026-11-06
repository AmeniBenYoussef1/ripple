"""Transparent scenario scoring. Agents propose scenarios; this code ranks them.

Order of decision (lexicographic, lower is better):
  0. hard constraints must all pass, otherwise the scenario is shown greyed out with reasons
  1. worst burden on any single group      (protect the group that loses most)
  2. total burden over all people          (people x minutes)
  3. extra cost in euros                   (unknown cost ranks after known costs)
  4. number of things still "to confirm"   (unverified availability is risk)

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

    def key(self) -> tuple:
        cost = self.extra_cost if self.extra_cost is not None else math.inf
        return (self.worst_burden, self.total_burden, cost, self.unconfirmed)

    def to_dict(self) -> dict:
        return {
            "id": self.scenario.id, "title": self.scenario.title, "feasible": self.feasible,
            "violations": self.violations, "worst_group": self.worst_group,
            "worst_burden_min": self.worst_burden, "total_burden_person_min": self.total_burden,
            "extra_cost_eur": self.extra_cost, "unconfirmed": self.unconfirmed,
            "rank": self.rank, "explanation": self.explanation,
        }


def hard_violations(s: Scenario, remaining_budget: float | None) -> list[str]:
    v = []
    if s.capacity is not None and s.attendees is not None and s.capacity < s.attendees:
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


def score(scenarios: list[Scenario], remaining_budget: float | None = None) -> list[ScoredScenario]:
    scored: list[ScoredScenario] = []
    for s in scenarios:
        viol = hard_violations(s, remaining_budget)
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
        ))

    feasible = sorted((x for x in scored if x.feasible), key=lambda x: x.key())
    for i, x in enumerate(feasible, start=1):
        x.rank = i
        x.explanation = [
            f"worst impact: {x.worst_group or 'nobody'}, {x.worst_burden} min per person",
            f"total impact: {x.total_burden} person-minutes",
            "extra cost: to quote" if x.extra_cost is None else f"extra cost: EUR {x.extra_cost:g}",
            f"still to confirm: {x.unconfirmed}",
        ]
    for x in scored:
        if not x.feasible:
            x.explanation = ["fails a hard limit: " + "; ".join(x.violations)]
    return feasible + [x for x in scored if not x.feasible]
