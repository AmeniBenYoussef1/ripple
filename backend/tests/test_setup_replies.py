"""The Impact Analyst's setup reply on the HerCode files, as it really came back on Nov 7.

Its first reply grouped the nodes by kind ({"resources": [...], "program": [...]}); after being asked
again it sent one list but with types like "venue" or none at all. Both were rejected, and the
organizer saw "Ripple couldn't find an event in these files". The facts were all there.
"""
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.agents import EventModelReply, parse_reply
from app.main import create_app
from app.models import EventModel
from app.orchestrator import FIXTURES, GENERATED_MODEL, model_checker
from app.visitors import Limits, Visitors

DATA = Path(__file__).parent / "data"
HERCODE = Path(__file__).resolve().parents[1] / "sample_pack"      # the HerCode files, as offered for download
FILES = ["participants.csv", "people.csv", "schedule.json", "catering_contract.pdf", "budget.json"]
GROUPED = (DATA / "hercode_reply_grouped_nodes.txt").read_text(encoding="utf-8")
FLAT_BAD_TYPES = (DATA / "hercode_reply_flat_bad_types.txt").read_text(encoding="utf-8")
GARBAGE = "Here is the event model you asked for: (sorry, I ran out of steps)"


class ScriptedChat:
    """Answers each agent message with the next reply of a script; $0.01 per answer."""

    def __init__(self, replies):
        self.replies, self.messages = list(replies), []

    async def upload_file(self, data, *, filename):
        return SimpleNamespace(id=filename)

    def _run(self, message):
        self.messages.append(message)
        text = self.replies.pop(0)

        class Result:
            status, correlation_id = "success", 1
            data = SimpleNamespace(cost=SimpleNamespace(total=Decimal("0.01")), description=None, message=None, filename=None)

        class Run:
            thread = SimpleNamespace(id=f"t{len(self.messages)}")

            def __aiter__(self):
                async def gen():
                    yield Result()
                return gen()

            async def result(self):
                return SimpleNamespace(content=text, questions=[], files=[])
        return Run()

    async def start(self, **kw):
        return self._run(kw["message"])

    async def send(self, thread_id, **kw):
        return self._run(kw["message"])


def make(tmp_path, replies):
    import os
    for env in ("COMINTY_AGENT_IMPACT", "COMINTY_AGENT_FINDER", "COMINTY_AGENT_REPLANNER", "COMINTY_AGENT_COORDINATOR"):
        os.environ[env] = "agent"
    chat = ScriptedChat(replies)
    visitors = Visitors(tmp_path / "usage.json", Limits(analyses=5))
    app = create_app(events_dir=tmp_path / "events", fixtures_dir=FIXTURES, model_path=GENERATED_MODEL, demo=False,
                     replay_delay=(0, 0), recordings_dir=tmp_path / "recordings", public=True,
                     visitors=visitors, client=SimpleNamespace(chat=chat))
    return app, chat, visitors


def analyze(c):
    eid = c.post("/api/visitor/event").json()["event_id"]
    files = [("files", (n, (HERCODE / n).read_bytes())) for n in FILES]
    return eid, c.post(f"/api/events/{eid}/analyze", files=files)


# ---- the replies themselves ------------------------------------------------------------------

def test_both_real_replies_are_read_as_the_same_event():
    for text in (GROUPED, FLAT_BAD_TYPES):
        reply = parse_reply(text, EventModelReply)
        model_checker(reply)
        m = reply.event_model
        assert len(m.nodes) == 19 and len(m.edges) == 38
        assert {n.type.value for n in m.nodes} == {"resource", "commitment", "schedule_item", "group", "constraint"}
        venue = m.node("venue_atelier_lumiere")
        assert venue.type.value == "resource" and venue.attrs["kind"] == "venue" and venue.attrs["capacity"] == 170
        # facts outside "attrs" are kept, not dropped
        assert m.node("opening").attrs == {"start": "09:00", "end": "09:30", "room": "Main hall", "speaker": "S1"}
        # two source files: the contract (with the caterer's email) is the one used for recipients
        caterer = m.node("grp_caterer")
        assert caterer.source == "catering_contract.pdf"
        assert caterer.attrs["sources"] == ["schedule.json", "catering_contract.pdf"]


def test_clean_models_are_unchanged():
    data = json.loads(GENERATED_MODEL.read_text(encoding="utf-8"))
    assert EventModel.model_validate(data).model_dump(mode="json", by_alias=True)["nodes"] == data["nodes"]


# ---- the whole setup, through the API --------------------------------------------------------

def test_hercode_files_with_the_real_reply_make_an_event(tmp_path):
    app, chat, visitors = make(tmp_path, [GROUPED])
    with TestClient(app) as c:
        eid, r = analyze(c)
        assert r.status_code == 200 and r.json()["status"] == "analyzed"
        groups = [n["id"] for n in r.json()["state"]["model"]["nodes"] if n["type"] == "group"]
        assert "grp_attendees" in groups and len(groups) == 7
        assert len(chat.messages) == 1                                     # read at once: no retry needed


def test_an_unusable_reply_is_tried_again_in_a_new_conversation(tmp_path):
    # first conversation: garbage, and garbage again after being asked; second conversation: fine
    app, chat, visitors = make(tmp_path, [GARBAGE, GARBAGE, GROUPED])
    with TestClient(app) as c:
        eid, r = analyze(c)
        assert r.status_code == 200
        state = r.json()["state"]
        retries = [l for l in state["log"] if l["kind"] == "retry"]
        assert len(retries) == 1 and "try 1 of 2" in retries[0]["text"] and "new conversation" in retries[0]["text"]
        assert "Expected shape" in chat.messages[1]                        # the re-ask spells out the format
        assert state["total_cost"] == "0.03"                               # the failed try is counted
        assert visitors.spent() == Decimal("0.03")
    failed = tmp_path / "recordings" / eid / "setup-live" / "setup_analyst-failed-1.json"
    assert json.loads(failed.read_text(encoding="utf-8"))["raw"] == GARBAGE   # kept to see why later


def test_two_unusable_conversations_show_the_message_and_still_count_the_cost(tmp_path):
    app, chat, visitors = make(tmp_path, [GARBAGE] * 4)
    with TestClient(app) as c:
        eid, r = analyze(c)
        assert r.status_code == 422 and r.json()["detail"].startswith("not-event|")
        state = c.get(f"/api/events/{eid}").json()
        retries = [l["text"] for l in state["log"] if l["kind"] == "retry"]
        assert len(retries) == 2 and "(try 1 of 2)" in retries[0] and retries[1].endswith("Stopped.")
        assert state["total_cost"] == "0.04" and visitors.spent() == Decimal("0.04")
