# Ripple JSON shapes

Edge direction: `from -> to` means "a change to `from` can affect `to`".

## 1. Event model (Impact Analyst, setup)
```json
{"event_model": {
  "event_id": "codesprint", "title": "CodeSprint",
  "nodes": [
    {"id": "venue_main_hall", "type": "resource", "label": "Main hall", "attrs": {"capacity": 220}, "source": "venue_contract.pdf"},
    {"id": "catering_delivery", "type": "commitment", "label": "Catering delivery", "attrs": {"time": "12:00", "quantity": 200}, "source": "catering_contract.pdf"},
    {"id": "lunch", "type": "schedule_item", "label": "Lunch", "attrs": {"start": "12:00", "end": "13:00"}},
    {"id": "grp_attendees", "type": "group", "label": "Attendees", "attrs": {"size": 200}, "source": "participants.csv"}
  ],
  "edges": [
    {"from": "venue_main_hall", "to": "catering_delivery", "type": "located_at"},
    {"from": "catering_delivery", "to": "lunch", "type": "depends_on"},
    {"from": "lunch", "to": "grp_attendees", "type": "must_inform"},
    {"from": "grp_attendees", "to": "venue_main_hall", "type": "sized_by"}
  ]
}}
```
- Node types: `resource`, `commitment`, `schedule_item`, `group`, `constraint`.
- Edge types: `located_at`, `scheduled_after`, `depends_on`, `must_inform`, `sized_by`, `constrained_by`.
- Ids: lowercase snake_case, unique; groups start with `grp_`.

## 2. Disruption (Impact Analyst, on an organizer message)
```json
{"disruption": {"node": "venue_main_hall", "kind": "lost", "detail": "Cancelled by the venue at 21:04", "params": {}}}
```
- Kinds: `lost`, `supplier_failed`, `delayed` (params: `delay_minutes`), `resized` (params: `new_size`), `cost_changed` (params: `new_value`).
- `node` must be an id from the event model you were given.

## 3. Venue / resource candidates (Resource Finder)
```json
{"candidates": [
  {"name": "...", "url": "https://...", "area": "...", "capacity": 250, "est_cost_eur": null,
   "fit_notes": "...", "availability": "to confirm"}
]}
```
- 3 to 5 candidates; `capacity` and `est_cost_eur` are `null` when the page doesn't state them.

## 4. Scenarios (Replanner)
```json
{"scenarios": [
  {"id": "B", "title": "Move to Hall C",
   "changes": [{"node": "venue_main_hall", "replace_with": "Hall C"}, {"node": "lunch", "set": {"start": "12:30"}}],
   "capacity": 250, "attendees": 200, "extra_cost_eur": null,
   "accessibility_ok": true, "within_opening_hours": true,
   "burdens": [{"group": "attendees", "people": 200, "minutes": 15, "must_replan": false}],
   "to_confirm": ["Hall C availability"]}
]}
```
- 2 or 3 scenarios. `burdens.minutes` is an estimate per person of time lost or extra travel.
- `must_replan: true` when people must reorganize their whole day (e.g. postponement).
- Do NOT rank the scenarios: Ripple's code scores them.

## 5. Messages (Coordinator)
```json
{"messages": [
  {"group": "grp_speakers", "to": "speaker2@example.com", "channel": "email",
   "subject": "New room for your workshop", "body": "...", "status": "draft"}
],
 "revised_schedule": [{"id": "lunch", "start": "12:30", "end": "13:30", "place": "Hall C"}],
 "volunteer_tasks": [{"who": "volunteer lead", "task": "...", "time": "07:30"}],
 "files": ["revised_schedule.ics", "attendee_update.csv"]}
```
- One message per affected group. Never send anything: everything stays `draft` until the organizer approves.
- Create the listed files in your sandbox and return them with your reply.
