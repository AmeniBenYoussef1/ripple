"""Event setup: files -> Impact Analyst -> event model. Replay over HTTP; Live with a fake client."""
import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import DEMO_FILES, create_app
from app.orchestrator import (DATA, FIXTURES, GENERATED_MODEL, OrchestratorError, Runner, analyze_event,
                              check_setup_files, new_event)


def demo_uploads():
    return [("files", (n, (DATA / n).read_bytes())) for n in DEMO_FILES]


@pytest.fixture
def client(tmp_path):
    app = create_app(events_dir=tmp_path / "events", fixtures_dir=FIXTURES, model_path=GENERATED_MODEL,
                     demo=True, replay_delay=(0, 0))
    with TestClient(app) as c:
        c.post("/api/events/demo/setup")
        yield c


def test_fresh_event_is_not_analyzed(client):
    s = client.get("/api/events/demo").json()
    assert s["setup"] == {"status": "not_analyzed"}


def test_replay_analyze_builds_the_model_from_the_recording(client):
    client.post("/api/events/demo/disruptions", json={"text": "Speaker 2's train is 2 hours late."})
    r = client.post("/api/events/demo/analyze", files=demo_uploads())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "analyzed"
    s = body["state"]
    assert s["setup"]["status"] == "analyzed" and s["setup"]["replay"] is True
    assert [f["name"] for f in s["setup"]["files"]] == DEMO_FILES
    assert s["history"] == []                                   # a new model starts the event over
    sizes = {n["id"]: n["attrs"].get("size") for n in s["model"]["nodes"] if n["type"] == "group"}
    assert sizes["grp_attendees"] == 200 and sizes["grp_volunteers"] == 12
    assert all(n.get("source") for n in s["model"]["nodes"])   # every fact names its file
    assert s["costs"] == [{"disruption": "setup", "agent": "impact_analyst", "cost": "0.010812070"}]
    # the demo still works on the analyzed model
    one = client.post("/api/events/demo/disruptions", json={"text": "The hall just cancelled."}).json()
    assert one["routing"]["run"] == ["resource_finder", "replanner", "coordinator"]


def test_confirm(client):
    client.post("/api/events/demo/analyze", files=demo_uploads())
    s = client.post("/api/events/demo/setup/confirm").json()
    assert s["setup"]["confirmed"] is True


def test_file_checks(client):
    too_many = demo_uploads() + [("files", ("extra.csv", b"a,b\n1,2\n"))]
    r = client.post("/api/events/demo/analyze", files=too_many)
    assert r.status_code == 400 and "at most 5 files" in r.json()["detail"]
    r = client.post("/api/events/demo/analyze", files=[("files", ("tool.exe", b"MZ"))])
    assert r.status_code == 400 and "CSV, PDF, TXT or JSON" in r.json()["detail"]
    r = client.post("/api/events/demo/analyze", files=[("files", ("empty.csv", b""))])
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    assert client.post("/api/events/demo/analyze").status_code == 422      # no files at all


def test_demo_files(client):
    files = client.get("/api/demo-files").json()
    assert [f["name"] for f in files] == DEMO_FILES and all(f["bytes"] > 0 for f in files)
    r = client.get("/api/demo-files/participants.csv")
    assert r.status_code == 200 and r.text.startswith("name,email,team")
    assert client.get("/api/demo-files/..%2F.env").status_code == 404
    assert client.get("/api/demo-files/event_model_generated.json").status_code == 404


def test_check_setup_files_rejects_oversized():
    with pytest.raises(OrchestratorError, match="10 MB"):
        check_setup_files([("big.csv", b"x" * (10 * 1024 * 1024 + 1))])


# ---- Live, with a fake Cominty client -----------------------------------------------------

class Result:
    def __init__(self):
        self.status = "success"
        self.correlation_id = 1
        self.data = SimpleNamespace(cost=SimpleNamespace(total=Decimal("0.011")), description=None,
                                    message=None, filename=None)


class FakeRun:
    def __init__(self, content):
        self.thread = SimpleNamespace(id="thread-setup")
        self._reply = SimpleNamespace(content=content, questions=[], files=[])

    def __aiter__(self):
        async def gen():
            yield Result()
        return gen()

    async def result(self):
        return self._reply


class FakeChat:
    def __init__(self, reply):
        self.uploads, self.started, self.reply = [], [], reply

    async def upload_file(self, data, *, filename):
        self.uploads.append((filename, len(data)))
        return SimpleNamespace(id=f"up-{len(self.uploads)}")

    async def start(self, **kw):
        self.started.append(kw)
        return FakeRun(self.reply)


def test_live_analyze_uploads_files_and_records_apart(tmp_path, monkeypatch):
    monkeypatch.setenv("COMINTY_AGENT_IMPACT", "agent-impact")
    model = json.loads(GENERATED_MODEL.read_text(encoding="utf-8"))
    chat = FakeChat(json.dumps({"event_model": model}))
    runner = Runner(client=SimpleNamespace(chat=chat), demo=False, fixtures_dir=tmp_path / "fx")
    event = new_event("live", events_dir=tmp_path / "events")
    files = [(n, (DATA / n).read_bytes()) for n in DEMO_FILES]
    out = asyncio.run(analyze_event(event, files, runner))
    assert out["status"] == "analyzed" and out["setup"]["replay"] is False
    assert [u[0] for u in chat.uploads] == DEMO_FILES
    assert chat.started[0]["file_ids"] == ["up-1", "up-2", "up-3", "up-4", "up-5"]
    assert "from the 5 attached files" in chat.started[0]["message"]
    assert (tmp_path / "fx" / "setup-live" / "setup_analyst.json").exists()   # never overwrites "setup"
    assert not (tmp_path / "fx" / "setup").exists()
    assert event.data["setup"]["cost"] == "0.011"
