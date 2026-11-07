"""Turn venue addresses into map pins (OpenStreetMap Nominatim), with a file cache.

Only real addresses are looked up: the Resource Finder copies them from the venue's
page, and a placeholder like "[FICTIONAL ADDRESS]" is never sent. Results (also misses)
are cached in a JSON file, so each address is asked once and DEMO_MODE works offline.
Nominatim's usage policy: an identifying User-Agent and at most 1 request per second.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "Ripple/0.1 (event-disruption co-pilot demo; geocodes a few venue addresses)"
CACHE_FILE = Path(__file__).resolve().parents[1] / "demo_data" / "geocache.json"

Point = Optional[tuple[float, float]]
Fetch = Callable[[str], Awaitable[Point]]


def is_real_address(text: Optional[str]) -> bool:
    return bool(text) and "[" not in text and len(text.strip()) >= 8


async def nominatim_fetch(query: str) -> Point:
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": USER_AGENT}) as http:
        r = await http.get(NOMINATIM, params={"q": query, "format": "json", "limit": 1})
        r.raise_for_status()
        hits = r.json()
    return (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None


class Geocoder:
    def __init__(self, cache_file: Path = CACHE_FILE, fetch: Fetch = nominatim_fetch) -> None:
        self.cache_file = cache_file
        self.fetch = fetch
        self.cache: dict[str, Optional[list[float]]] = (
            json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {})
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def locate(self, address: Optional[str]) -> Point:
        if not is_real_address(address):
            return None
        key = " ".join(address.split()).lower()
        if key in self.cache:
            hit = self.cache[key]
            return (hit[0], hit[1]) if hit else None
        async with self._lock:   # one request at a time, at most one per second
            wait = 1.0 - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                point = await self.fetch(address)
            except (httpx.HTTPError, ValueError, KeyError):
                return None   # not cached: a network error may be temporary
            finally:
                self._last = time.monotonic()
        self.cache[key] = [point[0], point[1]] if point else None
        self.cache_file.write_text(json.dumps(self.cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return point


def with_area(address: Optional[str], area: Optional[str]) -> Optional[str]:
    """"195 rue Saint-Jacques" + area "Paris 5e" -> "195 rue Saint-Jacques, Paris": a street alone is ambiguous."""
    if not address or not area:
        return address
    city = area.split("(")[0].split()[0].strip(",")      # "Paris 5e" -> "Paris"
    return address if city.lower() in address.lower() else f"{address}, {city}"


async def venue_pins(entry: dict[str, Any], model_nodes: list[dict[str, Any]], geo: Geocoder) -> dict[str, Any]:
    """Pins for the lost venue and each candidate of one disruption."""
    origin_id = (entry.get("disruption") or {}).get("node")
    origin = next((n for n in model_nodes if n["id"] == origin_id and n["type"] == "resource"), None)
    lost = None
    if origin is not None:
        address = origin.get("attrs", {}).get("address")
        point = await geo.locate(address)
        lost = {"name": origin.get("attrs", {}).get("original_label") or origin["label"], "address": address,
                "lat": point[0] if point else None, "lon": point[1] if point else None,
                "note": None if point else "fictional demo address: not shown on the map"
                if address and "[" in address else "no address known"}
    candidates = []
    for c in entry.get("candidates") or []:
        if c.get("lat") is not None and c.get("lon") is not None:
            point, how = (float(c["lat"]), float(c["lon"])), "from the venue page"
        else:
            point = await geo.locate(with_area(c.get("address"), c.get("area")))
            how = "geocoded from the address" if point else None
        candidates.append({"name": c["name"], "url": c["url"], "address": c.get("address"),
                           "area": c.get("area"), "capacity": c.get("capacity"),
                           "lat": point[0] if point else None, "lon": point[1] if point else None,
                           "located": how})
    return {"disruption": entry.get("id"), "lost": lost, "candidates": candidates}
