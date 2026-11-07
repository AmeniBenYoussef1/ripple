"""Public Start flow: visitor events, limits, cleanup and odd inputs. A fake Cominty client: no network."""
import json
import time
import zipfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.orchestrator import FIXTURES, GENERATED_MODEL
from app.visitors import Limits, Visitors, is_visitor_event

SAMPLE = Path(__file__).resolve().parents[1] / "sample_pack"
FILES = ["participants.csv", "people.csv", "schedule.json", "catering_contract.pdf", "budget.json"]
MODEL = json.loads(GENERATED_MODEL.read_text(encoding="utf-8"))


class FakeChat:
    """Answers every agent call with the same JSON reply; costs $0.01 per call."""

    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    async def upload_file(self, data, *, filename):
        return SimpleNamespace(id=filename)

    def _run(self):
        chat = self

        class Result:
            status, correlation_id = "success", 1
            data = SimpleNamespace(cost=SimpleNamespace(total=Decimal("0.01")), description=None, message=None, filename=None)

        class Run:
            thread = SimpleNamespace(id="t1")

            def __aiter__(self):
                async def gen():
                    yield Result()
                return gen()

            async def result(self):
                return SimpleNamespace(content=json.dumps(chat.reply), questions=[], files=[])
        self.calls += 1
        return Run()

    async def start(self, **kw):
        return self._run()

    async def send(self, thread_id, **kw):
        return self._run()


def make(tmp_path, reply=None, owner_token="", **limits):
    for env in ("COMINTY_AGENT_IMPACT", "COMINTY_AGENT_FINDER", "COMINTY_AGENT_REPLANNER", "COMINTY_AGENT_COORDINATOR"):
        import os
        os.environ[env] = "agent"
    chat = FakeChat(reply if reply is not None else {"event_model": MODEL})
    visitors = Visitors(tmp_path / "usage.json", Limits(**limits))
    app = create_app(events_dir=tmp_path / "events", fixtures_dir=FIXTURES, model_path=GENERATED_MODEL, demo=False,
                     replay_delay=(0, 0), recordings_dir=tmp_path / "recordings", public=True,
                     visitors=visitors, client=SimpleNamespace(chat=chat), owner_token=owner_token)
    return app, chat, visitors


def upload(names=FILES, folder=SAMPLE):
    return [("files", (n, (folder / n).read_bytes())) for n in names]


# ---- separate events, privacy ----------------------------------------------------------------

def test_each_visitor_gets_their_own_event_and_cannot_see_others(tmp_path):
    app, chat, _ = make(tmp_path)
    with TestClient(app) as a, TestClient(app) as b:
        ea = a.post("/api/visitor/event", json={"title": "Mine"}).json()["event_id"]
        assert is_visitor_event(ea) and "ripple_visitor" in a.cookies
        eb = b.post("/api/visitor/event").json()["event_id"]
        assert ea != eb
        assert a.get(f"/api/events/{ea}").status_code == 200
        assert b.get(f"/api/events/{ea}").status_code == 404                 # someone else's event
        assert [e["id"] for e in a.get("/api/events").json()] == [ea]       # public: only your own
        # shared events are read-only in public mode, and live agents never run on them
        assert a.post("/api/events/demo/reset").status_code == 403
        assert a.post("/api/events/demo/create").status_code == 403


def test_sample_copy_replays_codesprint_for_free_without_touching_it(tmp_path):
    app, chat, visitors = make(tmp_path)
    with TestClient(app) as c:
        s = c.post("/api/visitor/sample").json()
        eid = s["event_id"]
        assert s["demo_mode"] is True and s["replay_of"] == "demo" and s["model"]["nodes"]
        one = c.post(f"/api/events/{eid}/disruptions", json={"text": "The hall just cancelled."}).json()
        assert one["routing"]["run"] == ["resource_finder", "replanner", "coordinator"]
        assert chat.calls == 0 and visitors.spent() == 0                    # replayed: no agent, no cost
        assert not (tmp_path / "events" / "demo.json").exists()              # the shared demo isn't touched
        assert c.post(f"/api/events/{eid}/reset").json()["replay_of"] == "demo"


# ---- limits ----------------------------------------------------------------------------------

def test_one_live_analysis_per_visitor_then_a_friendly_refusal(tmp_path):
    app, chat, visitors = make(tmp_path)
    with TestClient(app) as c:
        e1 = c.post("/api/visitor/event").json()["event_id"]
        r = c.post(f"/api/events/{e1}/analyze", files=upload())
        assert r.status_code == 200 and r.json()["status"] == "analyzed"
        assert visitors.spent() == Decimal("0.01")                           # real Result costs
        st = c.get("/api/visitor/status").json()
        assert st["analyses_left"] == 0 and st["problems_left"] == 5 and st["live"] is True
        e2 = c.post("/api/visitor/event").json()["event_id"]
        r = c.post(f"/api/events/{e2}/analyze", files=upload())
        assert r.status_code == 429 and r.json()["detail"].startswith("limit|") and "sample event" in r.json()["detail"]
    # a new browser (no cookie) from the same IP is limited too
    with TestClient(app) as other:
        e3 = other.post("/api/visitor/event").json()["event_id"]
        assert other.post(f"/api/events/{e3}/analyze", files=upload()).status_code == 429


def test_problem_limit_and_daily_budget_stop_before_any_agent_runs(tmp_path):
    app, chat, visitors = make(tmp_path, problems=0)
    with TestClient(app) as c:
        eid = c.post("/api/visitor/event").json()["event_id"]
        c.post(f"/api/events/{eid}/analyze", files=upload())
        calls = chat.calls
        r = c.post(f"/api/events/{eid}/disruptions", json={"text": "The venue cancelled."})
        assert r.status_code == 429 and "problems" in r.json()["detail"] and chat.calls == calls
    app, chat, visitors = make(tmp_path / "b", budget_usd=Decimal("0"))
    with TestClient(app) as c:
        eid = c.post("/api/visitor/event").json()["event_id"]
        r = c.post(f"/api/events/{eid}/analyze", files=upload())
        assert r.status_code == 429 and "used up for today" in r.json()["detail"] and chat.calls == 0


def test_limits_reset_the_next_day(tmp_path):
    day = ["2026-11-07"]
    v = Visitors(tmp_path / "u.json", Limits(analyses=1), today=lambda: day[0])
    v.count("vis", "1.2.3.4", "analyses")
    v.add_cost(Decimal("2.5"))
    assert v.refusal("vis", "1.2.3.4", "analyses") is not None
    day[0] = "2026-11-08"
    assert v.refusal("vis", "1.2.3.4", "analyses") is None and v.spent() == 0
    assert "1.2.3.4" not in (tmp_path / "u.json").read_text()                # IPs are stored hashed


# ---- cleanup ---------------------------------------------------------------------------------

def test_visitor_events_are_deleted_after_the_ttl(tmp_path):
    now = [time.time()]
    v = Visitors(tmp_path / "u.json", Limits(ttl_hours=24), clock=lambda: now[0])
    events, rec = tmp_path / "events", tmp_path / "recordings"
    for eid in ("v-aaaaaaaaaa", "v-bbbbbbbbbb"):
        (events / eid / "inputs").mkdir(parents=True)
        (events / eid / "inputs" / "participants.csv").write_text("x")
        (events / f"{eid}.json").write_text("{}")
        (rec / eid).mkdir(parents=True)
    (events / "demo.json").write_text("{}")
    v.own("v-aaaaaaaaaa", "vis")
    now[0] += 25 * 3600
    v.own("v-bbbbbbbbbb", "vis")                                             # created a day later
    assert v.cleanup(events, rec) == ["v-aaaaaaaaaa"]
    assert not (events / "v-aaaaaaaaaa.json").exists() and not (events / "v-aaaaaaaaaa").exists()
    assert not (rec / "v-aaaaaaaaaa").exists()
    assert (events / "v-bbbbbbbbbb.json").exists() and (events / "demo.json").exists()


# ---- odd inputs ------------------------------------------------------------------------------

def test_odd_inputs_get_polite_messages(tmp_path):
    app, chat, visitors = make(tmp_path, reply={"event_model": {"event_id": "x", "nodes": [
        {"id": "recipe", "type": "resource", "label": "Pancake recipe"}]}})       # not an event: no group
    with TestClient(app) as c:
        eid = c.post("/api/visitor/event").json()["event_id"]
        assert c.post(f"/api/events/{eid}/analyze").status_code == 422          # empty form (no files)
        big = [("files", ("participants.csv", b"x" * (5 * 1024 * 1024 + 1)))]
        r = c.post(f"/api/events/{eid}/analyze", files=big)
        assert r.status_code == 400 and "larger than 5 MB" in r.json()["detail"]
        r = c.post(f"/api/events/{eid}/analyze", files=[("files", ("notes.docx", b"hi"))])
        assert r.status_code == 400 and "CSV, PDF, TXT or JSON" in r.json()["detail"]
        assert c.get("/api/visitor/status").json()["analyses_left"] == 1     # refused files cost no try
        r = c.post(f"/api/events/{eid}/analyze", files=[("files", ("recipes.txt", b"Pancakes: eggs, milk, flour"))])
        assert r.status_code == 422 and r.json()["detail"].startswith("not-event|")
        assert c.post(f"/api/events/{eid}/disruptions", json={"text": "   "}).status_code == 400   # empty problem


def test_vague_problem_gets_a_plain_message(tmp_path):
    app, chat, _ = make(tmp_path, problems=5)
    with TestClient(app) as c:
        eid = c.post("/api/visitor/event").json()["event_id"]
        c.post(f"/api/events/{eid}/analyze", files=upload())
        chat.reply = {"disruption": {"node": "nothing_like_this", "kind": "lost"}}   # the analyst can't place it
        r = c.post(f"/api/events/{eid}/disruptions", json={"text": "stuff happened"})
        assert r.status_code == 422 and r.json()["detail"].startswith("vague|")


def test_sample_pack_download(tmp_path):
    app, _, _ = make(tmp_path)
    with TestClient(app) as c:
        r = c.get("/api/sample-pack.zip")
        names = zipfile.ZipFile(BytesIO(r.content)).namelist()
        assert r.headers["content-type"] == "application/zip"
        assert {f"ripple-sample-pack/{n}" for n in FILES} <= set(names)


def test_cors_preflight_on_a_visitor_event_passes_without_a_cookie(tmp_path):
    app, _, _ = make(tmp_path)
    with TestClient(app) as c:
        eid = c.post("/api/visitor/event").json()["event_id"]
    with TestClient(app) as browser:                                   # preflights never carry cookies
        r = browser.options(f"/api/events/{eid}/setup/confirm", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173"


# ---- the owner -------------------------------------------------------------------------------

def test_owner_skips_visitor_limits_but_has_an_own_cap(tmp_path):
    app, chat, visitors = make(tmp_path, owner_token="s3cret-owner", analyses=0, problems=0,
                               budget_usd=Decimal("0"), owner_budget_usd=Decimal("0.01"))
    with TestClient(app) as c:
        assert c.post("/api/visitor/owner", json={"token": "wrong"}).json() == {"owner": False}
        assert "ripple_owner" not in c.cookies
        mark = c.post("/api/visitor/owner", json={"token": "s3cret-owner"}).json()
        assert mark["owner"] is True and mark["mark"] != "s3cret-owner"
        assert c.cookies["ripple_owner"] != "s3cret-owner"                    # a hash, never the token
        st = c.get("/api/visitor/status").json()
        assert st["owner"] is True and st["analyses_left"] is None and st["budget_left"] is True
        eid = c.post("/api/visitor/event").json()["event_id"]
        assert c.post(f"/api/events/{eid}/analyze", files=upload()).status_code == 200   # visitor limits are 0
        assert visitors.spent() == Decimal("0.01") and visitors.owner_spent() == Decimal("0.01")  # both totals
        r = c.post(f"/api/events/{eid}/disruptions", json={"text": "The venue cancelled."})
        assert r.status_code == 429 and "owner budget" in r.json()["detail"]   # the owner's own cap
    with TestClient(app) as stranger:                                          # no cookie: a normal visitor
        eid = stranger.post("/api/visitor/event").json()["event_id"]
        assert stranger.post(f"/api/events/{eid}/analyze", files=upload()).status_code == 429


def test_owner_header_works_and_is_off_without_a_token(tmp_path):
    app, _, _ = make(tmp_path, owner_token="s3cret-owner", analyses=0)
    with TestClient(app) as c:
        eid = c.post("/api/visitor/event").json()["event_id"]
        r = c.post(f"/api/events/{eid}/analyze", files=upload(), headers={"X-Ripple-Owner": "s3cret-owner"})
        assert r.status_code == 200
    app, _, _ = make(tmp_path / "off", owner_token="")
    with TestClient(app) as c:
        assert c.post("/api/visitor/owner", json={"token": ""}).json() == {"owner": False}
        assert c.get("/api/visitor/status").json()["owner"] is False


def test_visitor_id_in_a_header_works_without_cookies(tmp_path):
    """Frontend and backend on different sites: browsers may drop the cookie, the header still works."""
    app, _, _ = make(tmp_path)
    me = {"X-Ripple-Visitor": "ab" * 16}
    with TestClient(app) as a, TestClient(app) as b:
        eid = a.post("/api/visitor/event", headers=me).json()["event_id"]
        a.cookies.clear()
        assert a.get(f"/api/events/{eid}", headers=me).status_code == 200
        assert b.get(f"/api/events/{eid}?visitor={'ab' * 16}").status_code == 200       # links and live updates
        assert b.get(f"/api/events/{eid}").status_code == 404
