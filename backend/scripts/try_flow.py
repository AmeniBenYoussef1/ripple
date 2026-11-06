"""Run the 3 demo disruptions in order on a fresh event.

Run:  cd backend && .venv/Scripts/python scripts/try_flow.py ["search area"] [--record-missing]
Live by default (records fixtures/); with DEMO_MODE=true in .env it replays them for free.
--record-missing: replay what is recorded and call Cominty only for missing recordings.
For disruption 1 the top-ranked feasible scenario is chosen and the Coordinator runs.
"""
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env")
sys.stdout.reconfigure(encoding="utf-8")  # accents in venue names on the Windows console

from cominty_sdk import AsyncCominty, ComintyError  # noqa: E402

from app.agents import AgentOutputError  # noqa: E402
from app.orchestrator import (OrchestratorError, Runner, choose_scenario, demo_mode,  # noqa: E402
                              handle_disruption, new_event)

TEXTS = ["The hall just cancelled.", "Speaker 2's train is 2 hours late.", "30 late registrations."]
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
AREA = ARGS[0] if ARGS else "Paris, France"
RECORD_MISSING = "--record-missing" in sys.argv


def show_event(e: dict) -> None:
    if (e["event"] == "ToolCall" and e["status"] == "running") or e["event"] == "Replay":
        print(f"      · {e['agent']}: {e['description']}")


def show_disruption(out: dict) -> None:
    if out["status"] == "questions":
        print("  Impact Analyst asked:", [q["prompt"] for q in out["questions"]])
        return
    d = out["disruption"]
    print(f"  disruption: {d['node']} / {d['kind']} {d['params'] or ''}  ({d['detail']})")
    for i, w in enumerate(out["impact"]["waves"], 1):
        print(f"  wave {i}: {', '.join(w)}")
    if out["impact"]["checked_ok"]:
        print(f"  checked in code, still OK: {out['impact']['checked_ok']}")
    r = out["routing"]
    print(f"  run: {r['run']}")
    for a, why in r["skipped"].items():
        print(f"  skipped: {a} ({why})")
    for a in r["cached"]:
        print(f"  cached: {a}")
    for c in out["candidates"]:
        print(f"  candidate: {c['name']} | cap {c.get('capacity')} | {c.get('area')} | {c['url']}")
    for s in out["scored"]:
        tag = f"#{s['rank']}" if s["feasible"] else "infeasible"
        tag += " tied" if s.get("tied") else ""
        print(f"  scenario {s['id']} [{tag}] {s['title']}")
        for line in s["explanation"]:
            print(f"      {line}")
    print(f"  coordinator: {out['coordinator']}")
    if out.get("questions"):
        print("  questions:", [q["prompt"] for q in out["questions"]])
    print(f"  cost: {out['costs']} | saved estimate: {out['saved']} = {out['saved_total']}")
    if out["coordinator"] == "done":    # ran at once: nothing to choose
        show_outbox({"chosen": None, "cost": out["costs"].get("coordinator"), **out})


def show_outbox(res: dict) -> None:
    print(f"  chosen: {res['chosen']} | coordinator cost {res['cost']}")
    for m in res["outbox"]:
        print(f"  [{m['status']}] {m['group']} -> {m['to']} ({m['channel']}): {m['subject']}")
    print(f"  revised schedule: {len(res['revised_schedule'])} items | volunteer tasks: {len(res['volunteer_tasks'])}")
    for f in res["files"]:
        size = f"{f['rows']} people" if "rows" in f else f"{f['items']} items"
        print(f"  file (built in code): {f['name']} | {size}")


async def flow(runner: Runner) -> None:
    event = new_event("demo-flow")
    for i, text in enumerate(TEXTS, 1):
        print(f"\n=== {i}. {text}")
        out = await handle_disruption(event, text, runner, area=AREA)
        show_disruption(out)
        if i == 1 and out["status"] == "ready":
            top = next((s for s in out["scored"] if s["feasible"]), None)
            if top is None:
                print("  no feasible scenario to choose")
                continue
            print(f"\n  --- organizer picks the top-ranked scenario: {top['id']}")
            show_outbox(await choose_scenario(event, top["id"], runner))
    total = sum(Decimal(c["cost"]) for c in event.data["costs"])
    saved = sum(Decimal(h.get("saved_total", "0")) for h in event.data["history"])
    print(f"\nTOTAL cost: {total} | saved by skipping/caching: {saved} | "
          f"{'replayed (DEMO_MODE)' if runner.demo else 'live'} | event file: {event.path.relative_to(BACKEND)}")


async def main() -> None:
    if demo_mode():
        await flow(Runner(demo=True, on_event=show_event))
        return
    async with AsyncCominty() as client:
        await flow(Runner(client=client, demo=False, on_event=show_event, prefer_recordings=RECORD_MISSING))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ComintyError, OrchestratorError, AgentOutputError) as e:
        print(f"\nERROR {type(e).__name__}: {e}")
