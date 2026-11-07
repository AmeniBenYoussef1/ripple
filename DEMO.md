# Ripple demo script (recorded path)

The CodeSprint 2026 sample event (fictional, 200 attendees, Paris) replays real, recorded
Cominty agent runs: it is instant and costs nothing. Anything outside this path was not
recorded, and Ripple says "Live mode only".

## Start

1. Landing page → **Get started** → the Start screen.
2. Click **Use a sample event**, pick problem 1, then **Start Ripple**.
   (Or upload your own fictional files: that runs the agents live, within the visitor limits.)

## Problem 1: "The hall just cancelled."

- The ripple map: the hall at the centre; schedule, caterer, speakers, volunteers and attendees on the rings.
- All 3 agents run: Resource Finder (venues with source links, "to confirm"), Replanner (3 options), Coordinator.
- Code scores the options. **Choose option B: Maison de l'Océan, Grand Amphithéâtre (500 seats).**
  - Option A (Cité de l'architecture) is recorded too; option C is Live mode only.
- The outbox: one draft per person or group, each saying only what changed for them. Approve all.
- Exports: revised schedule (.ics) and attendee update (.csv).

## Problem 2: "Speaker 2's train is 2 hours late."

- Only the Replanner and Coordinator run. **The Resource Finder is skipped** (no venue is affected).
- Only the 40 workshop participants and Speaker 2 are informed.
- **Choose option B: swap the workshop with another session that can start at 10:00.** (A and C: Live mode only.)

## Problem 3: "30 late registrations."

- Attendees grow to 230. The new hall fits (500 seats), so the venue is unaffected.
- **Only the Coordinator runs**: the caterer (new quantities) and the 30 newcomers.
- The "Saved" counter shows the cost of the agents that slept.

## What-if

- Open **What-if** and change a number (e.g. attendees or budget): the impact and the scores are
  recomputed by code only, with no agent call.

## Public deployment

- Backend env: `DEMO_MODE=false RIPPLE_PUBLIC=true`. The sample event always replays.
- Visitor limits (`.env`): `RIPPLE_VISITOR_ANALYSES=1`, `RIPPLE_VISITOR_PROBLEMS=5`,
  `RIPPLE_DAILY_BUDGET_USD=2`, `RIPPLE_VISITOR_TTL_HOURS=24`, `RIPPLE_VISITOR_MAX_FILE_MB=5`.
- Visitor events and uploads are deleted after 24 hours.
