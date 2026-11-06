"""Try the setup step live: 5 demo files -> Impact Analyst -> EventModel.

Run:  cd backend && .venv/Scripts/python scripts/try_setup.py
Saves demo_data/event_model_generated.json and compares it with the hand-made
demo_data/event_model.json. Then runs the 3 demo disruptions through the impact
engine on the generated model (code only, no credits). Costs one Impact Analyst run.
"""
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env")
sys.stdout.reconfigure(encoding="utf-8")  # accents in venue names on the Windows console

from cominty_sdk import AsyncCominty, ComintyError  # noqa: E402

from app.agents import AgentOutputError, build_event_model  # noqa: E402
from app.impact_engine import propagate, route  # noqa: E402
from app.models import Disruption, EventModel  # noqa: E402

DATA = BACKEND / "demo_data"
FILES = ["participants.csv", "schedule.json", "catering_contract.txt", "people.csv", "budget.json"]
OUT = DATA / "event_model_generated.json"

SETUP_MESSAGE = """Setup. Build the event model for this event from the 5 attached files.
Reply with {"event_model": {...}} as described in the ripple-contracts skill.

Conventions:
- event_id "codesprint". Reuse the ids given in schedule.json for commitments and program items.
- The venue is a resource with id "venue_<name in snake_case>", with capacity and address in attrs.
- Groups: one per audience that must be informed (attendees, speakers, each speaker with their own
  session, workshop participants, volunteers, the caterer). Ids start with "grp_". Put the real size
  (count the rows in the files) in attrs.size.
- The budget is a constraint node with id "budget" and attrs.remaining_eur.
- Edges (from -> to means a change to `from` can affect `to`):
  located_at: venue -> commitments and sessions held there.
  depends_on: prerequisite commitment -> the program item that needs it.
  scheduled_after: each main program item -> the next one (the workshop runs in parallel, not in the chain).
  must_inform: commitment or program item -> each group that must hear about a change to it.
  sized_by: the attendees group -> the venue and the catering whose size depends on it.
  constrained_by: budget -> the commitments it pays for.
- Put the source file name in "source" for every fact taken from a file."""

DEMOS = [
    ("1. Hall cancelled", Disruption(node="venue_main_hall", kind="lost"), None),
    ("2. Speaker 2 late", Disruption(node="workshop_s2", kind="delayed", params={"delay_minutes": 120}), None),
    ("3. +30 registrations (hall 250)",
     Disruption(node="grp_attendees", kind="resized", params={"new_size": 230}), 250),
]
EXPECTED_RUN = [["resource_finder", "replanner", "coordinator"], ["replanner", "coordinator"], ["coordinator"]]


def edge_set(m: EventModel) -> set[tuple[str, str, str]]:
    return {(e.from_id, e.type.value, e.to_id) for e in m.edges}


def compare(ref: EventModel, gen: EventModel) -> None:
    ref_nodes = {n.id: n for n in ref.nodes}
    gen_nodes = {n.id: n for n in gen.nodes}
    print(f"nodes: reference {len(ref_nodes)}, generated {len(gen_nodes)}")
    print("  missing:", sorted(ref_nodes.keys() - gen_nodes.keys()) or "none")
    print("  extra:  ", sorted(gen_nodes.keys() - ref_nodes.keys()) or "none")
    wrong_type = [f"{i} ({ref_nodes[i].type.value} -> {gen_nodes[i].type.value})"
                  for i in ref_nodes.keys() & gen_nodes.keys() if ref_nodes[i].type != gen_nodes[i].type]
    print("  wrong type:", wrong_type or "none")
    for nid in ("venue_main_hall", "catering_delivery", "grp_attendees", "grp_workshop", "grp_volunteers", "budget"):
        if nid in gen_nodes:
            print(f"  {nid}: attrs={gen_nodes[nid].attrs} source={gen_nodes[nid].source}")

    ref_e, gen_e = edge_set(ref), edge_set(gen)
    print(f"edges: reference {len(ref_e)}, generated {len(gen_e)}, matching {len(ref_e & gen_e)}")
    for label, edges in (("missing", ref_e - gen_e), ("extra", gen_e - ref_e)):
        print(f"  {label}:", "none" if not edges else "")
        for f, t, to in sorted(edges):
            print(f"    {f} -{t}-> {to}")


def check_demos(gen: EventModel) -> None:
    print("demo disruptions on the generated model (code only):")
    ids = {n.id for n in gen.nodes}
    for (label, d, capacity), expected in zip(DEMOS, EXPECTED_RUN):
        if d.node not in ids:
            print(f"  {label}: node {d.node} missing -> cannot run")
            continue
        m = gen.model_copy(deep=True)
        if capacity is not None and "venue_main_hall" in ids:
            m.node("venue_main_hall").attrs["capacity"] = capacity
        r = route(m, propagate(m, d))
        verdict = "OK" if r.run == expected else f"DIFFERENT (expected {expected})"
        print(f"  {label}: run={r.run} skipped={list(r.skipped)} -> {verdict}")


def print_event(e: dict) -> None:
    if e["status"] == "running":   # each step arrives twice; show it once
        print(f"  [{e['event']}] {e['description']}")


async def main() -> None:
    ref = EventModel.model_validate(json.loads((DATA / "event_model.json").read_text(encoding="utf-8")))
    async with AsyncCominty() as client:
        file_ids = []
        for name in FILES:
            up = await client.chat.upload_file(DATA / name)
            file_ids.append(up.id)
            print(f"uploaded {name} -> {up.id}")
        print("Impact Analyst:")
        try:
            res = await build_event_model(client, SETUP_MESSAGE, file_ids, on_event=print_event,
                                          thread_name="Setup: CodeSprint", max_steps=12)
        except AgentOutputError as err:
            print("FAILED:", err)
            return
    print(f"thread {res.thread_id} | cost {res.cost}")
    if res.questions:
        print("The agent asked questions instead:")
        for q in res.questions:
            print(f"  - {q.prompt} {q.options}")
        return

    gen = res.data.event_model
    OUT.write_text(gen.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
    print(f"saved {OUT.relative_to(BACKEND)}\n")
    compare(ref, gen)
    print()
    check_demos(gen)
    print(f"\ntotal cost: {res.cost}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ComintyError as e:
        print(f"Cominty error: {type(e).__name__}: {e}")
