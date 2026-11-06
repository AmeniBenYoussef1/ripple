"""Venue pins: only real addresses are looked up, results are cached. No network: fake fetch."""
import asyncio
import json

import httpx

from app.geo import Geocoder, is_real_address, venue_pins


def run(coro):
    return asyncio.run(coro)


def fake(calls, answers):
    async def fetch(q):
        calls.append(q)
        return answers.get(q)
    return fetch


def test_placeholders_are_never_sent(tmp_path):
    calls = []
    g = Geocoder(tmp_path / "cache.json", fake(calls, {}))
    assert run(g.locate("[FICTIONAL ADDRESS]")) is None
    assert run(g.locate(None)) is None and run(g.locate("Paris")) is None   # too vague
    assert calls == []
    assert not is_real_address("[FICTIONAL ADDRESS]") and is_real_address("6 Rue Jouffroy, 75017 Paris")


def test_results_and_misses_are_cached_on_disk(tmp_path):
    calls = []
    cache = tmp_path / "cache.json"
    g = Geocoder(cache, fake(calls, {"1 Rue A, Paris": (48.8, 2.3)}))
    assert run(g.locate("1 Rue A, Paris")) == (48.8, 2.3)
    assert run(g.locate("1  rue a,  paris")) == (48.8, 2.3)        # same key after normalising
    assert run(g.locate("99 Nowhere Street")) is None
    assert run(g.locate("99 Nowhere Street")) is None
    assert calls == ["1 Rue A, Paris", "99 Nowhere Street"]
    again = Geocoder(cache, fake(calls, {}))                         # a new process reads the file
    assert run(again.locate("1 Rue A, Paris")) == (48.8, 2.3)
    assert json.loads(cache.read_text())["99 nowhere street"] is None


def test_network_errors_are_not_cached(tmp_path):
    async def broken(q):
        raise httpx.ConnectError("offline")
    g = Geocoder(tmp_path / "cache.json", broken)
    assert run(g.locate("1 Rue A, Paris")) is None
    assert g.cache == {}


def test_venue_pins_prefers_page_coordinates(tmp_path):
    calls = []
    g = Geocoder(tmp_path / "c.json", fake(calls, {"2 Rue B, Paris": (48.9, 2.4)}))
    entry = {"id": "d1", "disruption": {"node": "hall"}, "candidates": [
        {"name": "On page", "url": "https://a.example.com", "lat": 48.1, "lon": 2.1, "address": "x street 1"},
        {"name": "Geocoded", "url": "https://b.example.com", "address": "2 Rue B, Paris"},
        {"name": "No address", "url": "https://c.example.com", "area": "Paris 9e"},
    ]}
    nodes = [{"id": "hall", "type": "resource", "label": "Main hall", "attrs": {"address": "[FICTIONAL ADDRESS]"}}]
    out = run(venue_pins(entry, nodes, g))
    assert out["lost"]["lat"] is None and "fictional" in out["lost"]["note"]
    by = {c["name"]: c for c in out["candidates"]}
    assert (by["On page"]["lat"], by["On page"]["located"]) == (48.1, "from the venue page")
    assert (by["Geocoded"]["lat"], by["Geocoded"]["located"]) == (48.9, "geocoded from the address")
    assert by["No address"]["lat"] is None and by["No address"]["located"] is None
    assert calls == ["2 Rue B, Paris"]                               # area alone is never geocoded


def test_street_without_city_gets_the_area_city():
    from app.geo import with_area
    assert with_area("195 rue Saint-Jacques", "Paris 5e") == "195 rue Saint-Jacques, Paris"
    assert with_area("4 square Rapp, 75007 Paris, France", "Paris 7e") == "4 square Rapp, 75007 Paris, France"
    assert with_area("28 rue Saint-Dominique", None) == "28 rue Saint-Dominique"
