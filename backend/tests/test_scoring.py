from app.models import GroupBurden, Scenario
from app.scoring import REPLAN_MINUTES, score


def demo_scenarios():
    split = Scenario(id="A", title="Split across Rooms B and C", capacity=160, attendees=200,
                     burdens=[GroupBurden(group="attendees", people=200, minutes=5)])
    hall_c = Scenario(id="B", title="Move to Hall C", capacity=250, attendees=200, extra_cost_eur=None,
                      burdens=[GroupBurden(group="attendees", people=200, minutes=15),
                               GroupBurden(group="volunteers", people=12, minutes=10)],
                      to_confirm=["Hall C availability", "caterer can deliver to Hall C"])
    postpone = Scenario(id="C", title="Postpone one week", extra_cost_eur=0,
                        burdens=[GroupBurden(group="attendees", people=200, must_replan=True)])
    return [split, hall_c, postpone]


def test_capacity_violation_is_greyed_out_last():
    ranked = score(demo_scenarios())
    a = ranked[-1]
    assert a.scenario.id == "A" and not a.feasible and a.rank is None
    assert "capacity 160 for 200 people" in a.explanation[0]


def test_protecting_worst_group_beats_cheaper_option():
    ranked = score(demo_scenarios())
    assert [x.scenario.id for x in ranked if x.feasible] == ["B", "C"]
    assert ranked[0].worst_burden == 15
    assert ranked[1].worst_burden == REPLAN_MINUTES


def test_budget_is_a_hard_limit():
    s = Scenario(id="X", title="Expensive hall", extra_cost_eur=900,
                 burdens=[GroupBurden(group="attendees", people=200, minutes=5)])
    ranked = score([s], remaining_budget=500)
    assert not ranked[0].feasible
    assert "over remaining budget" in ranked[0].violations[0]


def test_ties_broken_by_total_then_cost_then_unconfirmed():
    base = dict(burdens=[GroupBurden(group="g", people=10, minutes=20)])
    s1 = Scenario(id="1", title="known cost", extra_cost_eur=100, **base)
    s2 = Scenario(id="2", title="unknown cost", extra_cost_eur=None, **base)
    s3 = Scenario(id="3", title="cheap but unconfirmed", extra_cost_eur=100, to_confirm=["x"], **base)
    assert [x.scenario.id for x in score([s2, s3, s1])] == ["1", "3", "2"]


def test_explanation_is_human_readable():
    top = score(demo_scenarios())[0]
    assert top.explanation[0] == "worst impact: attendees, 15 min per person"
    assert top.explanation[2] == "extra cost: to quote"
