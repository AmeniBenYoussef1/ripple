"""Public Live mode guardrails: who may still run agents today, and cleaning up after visitors.

A visitor is a session cookie; their IP is also counted (hashed, never stored as such), so
clearing cookies doesn't reset the limits. Counts and the day's real spending (sum of Cominty
Result costs) live in one small JSON file. Everything resets at midnight (server time).
Visitor events, their uploads, files and recordings are deleted after RIPPLE_VISITOR_TTL_HOURS.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

VISITOR_EVENT = re.compile(r"^v-[a-f0-9]{10}$")


def is_visitor_event(event_id: str) -> bool:
    return bool(VISITOR_EVENT.match(event_id))


def new_visitor_event_id() -> str:
    return f"v-{secrets.token_hex(5)}"


@dataclass
class Limits:
    analyses: int = 1          # file analyses per visitor per day
    problems: int = 5          # problems per visitor per day
    budget_usd: Decimal = Decimal("2")   # all visitors together, per day
    ttl_hours: float = 24      # visitor events are deleted after this
    max_file_mb: float = 5

    @classmethod
    def from_env(cls) -> "Limits":
        env = os.environ.get
        return cls(analyses=int(env("RIPPLE_VISITOR_ANALYSES", "1")), problems=int(env("RIPPLE_VISITOR_PROBLEMS", "5")),
                   budget_usd=Decimal(env("RIPPLE_DAILY_BUDGET_USD", "2")),
                   ttl_hours=float(env("RIPPLE_VISITOR_TTL_HOURS", "24")),
                   max_file_mb=float(env("RIPPLE_VISITOR_MAX_FILE_MB", "5")))


@dataclass
class Visitors:
    path: Path
    limits: Limits = field(default_factory=Limits)
    clock: Callable[[], float] = time.time
    today: Callable[[], str] = lambda: date.today().isoformat()

    def __post_init__(self) -> None:
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.data.setdefault("salt", secrets.token_hex(16))
        self.data.setdefault("owners", {})        # event id -> {"visitor", "created"}
        self._roll()

    # ---- storage ----
    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")

    def _roll(self) -> None:
        """A new day: counts and spending start over."""
        if self.data.get("day") != self.today():
            self.data.update(day=self.today(), spent="0", visitors={}, ips={})
            self._save()

    def ip_key(self, ip: str) -> str:
        return hashlib.sha256(f"{self.data['salt']}|{ip}".encode()).hexdigest()[:16]

    def _counts(self, visitor: str, ip: str) -> tuple[dict, dict]:
        self._roll()
        v = self.data["visitors"].setdefault(visitor, {"analyses": 0, "problems": 0})
        i = self.data["ips"].setdefault(self.ip_key(ip), {"analyses": 0, "problems": 0})
        return v, i

    # ---- limits ----
    def spent(self) -> Decimal:
        self._roll()
        return Decimal(self.data["spent"])

    def refusal(self, visitor: str, ip: str, kind: str) -> Optional[str]:
        """Why this visitor can't run agents now, in plain words (None = go ahead)."""
        if self.spent() >= self.limits.budget_usd:
            return "Ripple's free live tries are used up for today. You can still explore a sample event, which replays real agent runs."
        if kind in ("analyses", "problems"):
            v, i = self._counts(visitor, ip)
            limit = getattr(self.limits, kind)
            if v[kind] >= limit or i[kind] >= limit:
                what = "file analysis" if kind == "analyses" else "problems"
                return (f"You've used today's free live {what} ({limit}). You can still explore a sample event, "
                        "which replays real agent runs, or come back tomorrow.")
        return None

    def count(self, visitor: str, ip: str, kind: str) -> None:
        v, i = self._counts(visitor, ip)
        v[kind] += 1
        i[kind] += 1
        self._save()

    def add_cost(self, amount: Decimal) -> None:
        if amount > 0:
            self._roll()
            self.data["spent"] = str(Decimal(self.data["spent"]) + amount)
            self._save()

    def status(self, visitor: str, ip: str) -> dict:
        v, i = self._counts(visitor, ip)
        left = lambda k: max(0, getattr(self.limits, k) - max(v[k], i[k]))
        return {"analyses_left": left("analyses"), "problems_left": left("problems"),
                "budget_left": self.spent() < self.limits.budget_usd,
                "max_file_mb": self.limits.max_file_mb, "ttl_hours": self.limits.ttl_hours}

    # ---- whose event ----
    def own(self, event_id: str, visitor: str) -> None:
        self.data["owners"][event_id] = {"visitor": visitor, "created": self.clock()}
        self._save()

    def owner(self, event_id: str) -> Optional[str]:
        return (self.data["owners"].get(event_id) or {}).get("visitor")

    def events_of(self, visitor: str) -> list[str]:
        return [e for e, o in self.data["owners"].items() if o["visitor"] == visitor]

    # ---- cleanup ----
    def cleanup(self, events_dir: Path, recordings_dir: Path) -> list[str]:
        """Delete visitor events (json, uploads, built files, recordings) older than the TTL."""
        cutoff = self.clock() - self.limits.ttl_hours * 3600
        removed = []
        for f in events_dir.glob("v-*.json"):
            eid = f.stem
            if not is_visitor_event(eid):
                continue
            created = (self.data["owners"].get(eid) or {}).get("created", f.stat().st_mtime)
            if created < cutoff:
                f.unlink(missing_ok=True)
                shutil.rmtree(events_dir / eid, ignore_errors=True)
                shutil.rmtree(recordings_dir / eid, ignore_errors=True)
                self.data["owners"].pop(eid, None)
                removed.append(eid)
        # owners whose files are already gone
        for eid in [e for e, o in self.data["owners"].items() if o["created"] < cutoff]:
            self.data["owners"].pop(eid, None)
        self._save()
        return removed
