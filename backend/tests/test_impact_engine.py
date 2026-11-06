import json
from pathlib import Path

import pytest

from app.impact_engine import propagate, route
from app.models import Disruption, EventModel

DEMO = Path(__file__).resolve().parents[1] / "demo_data" / "event_model.json"
UNTOUCHED = {"contest_problems", "online_judge", "prizes"}


@pytest.fixture
def model() -> EventModel:
    return EventModel.model_validate(json.loads(DEMO.read_text()))


# ---- disruption 1: venue cancelled -> full ripple ---------------------------

def test_venue_lost_reaches_everything_connected(model):
    imp = propagate(model, Disruption(node="venue_main_hall", kind="lost"))
    assert imp.waves[0] == ["av_setup", "catering_delivery", "volunteer_meetup", "workshop_s2"]
    affected = set(imp.affected)
    for g in ["grp_attendees", "grp_speakers", "grp_volunteers", "grp_caterer", "grp_workshop", "grp_speaker_2"]:
        assert g in affected
    assert {"opening", "lunch", "round_1", "round_2", "awards"} <= affected
    assert not (affected & UNTOUCHED)
    assert imp.effects["venue_main_hall"] == "lost"


def test_venue_lost_wakes_all_three_agents(model):
    imp = propagate(model, Disruption(node="venue_main_hall", kind="lost"))
    r = route(model, imp)
    assert r.run == ["resource_finder", "replanner", "coordinator"]
    assert r.skipped == {}


# ---- disruption 2: speaker delayed -> small ripple --------------------------

def test_speaker_delay_only_touches_workshop_people(model):
    imp = propagate(model, Disruption(node="workshop_s2", kind="delayed", params={"delay_minutes": 120}))
    assert imp.waves == [["grp_workshop", "grp_speaker_2"]]
    assert "venue_main_hall" not in imp.effects


def test_speaker_delay_skips_resource_finder(model):
    imp = propagate(model, Disruption(node="workshop_s2", kind="delayed"))
    r = route(model, imp)
    assert r.run == ["replanner", "coordinator"]
    assert "resource_finder" in r.skipped


# ---- disruption 3: late registrations -> capacity check in code ------------

def test_growth_that_still_fits_skips_finder_and_replanner(model):
    model.node("venue_main_hall").attrs["capacity"] = 250  # after the move to the bigger hall
    imp = propagate(model, Disruption(node="grp_attendees", kind="resized", params={"new_size": 230}))
    assert imp.checked_ok == ["venue_main_hall"]
    assert "venue_main_hall" not in imp.effects
    assert imp.effects["catering_delivery"] == "resize"
    assert "grp_caterer" in imp.affected
    r = route(model, imp)
    assert r.run == ["coordinator"]
    assert set(r.skipped) == {"resource_finder", "replanner"}


def test_growth_that_does_not_fit_wakes_finder(model):
    imp = propagate(model, Disruption(node="grp_attendees", kind="resized", params={"new_size": 230}))
    assert imp.effects["venue_main_hall"] == "insufficient"
    assert "resource_finder" in route(model, imp).run


def test_unknown_capacity_is_treated_as_affected(model):
    model.node("venue_main_hall").attrs.pop("capacity")
    imp = propagate(model, Disruption(node="grp_attendees", kind="resized", params={"new_size": 230}))
    assert imp.effects["venue_main_hall"] == "insufficient"


# ---- engine properties ------------------------------------------------------

def test_groups_do_not_propagate_further(model):
    imp = propagate(model, Disruption(node="opening", kind="delayed"))
    assert "grp_attendees" in imp.affected
    assert "venue_main_hall" not in imp.affected  # no edge back through the group


def test_each_node_appears_once(model):
    imp = propagate(model, Disruption(node="venue_main_hall", kind="lost"))
    assert len(imp.affected) == len(set(imp.affected))


def test_cache_skips_agent_with_identical_input(model):
    imp = propagate(model, Disruption(node="workshop_s2", kind="delayed"))
    first = route(model, imp)
    second = route(model, imp, cache=first.hashes)
    assert second.run == []
    assert set(second.cached) == {"replanner", "coordinator"}


def test_unknown_node_raises(model):
    with pytest.raises(KeyError):
        propagate(model, Disruption(node="ghost", kind="lost"))
