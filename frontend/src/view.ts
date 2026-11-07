// Turns a saved disruption (Entry) or live stream messages into what the screen shows (View).
import type { AgentName, AgentView, Entry, EventModel, View } from "./types";

export const AGENTS: { name: AgentName; title: string; short: string }[] = [
  { name: "impact_analyst", title: "Impact Analyst", short: "IA" },
  { name: "resource_finder", title: "Resource Finder", short: "RF" },
  { name: "replanner", title: "Replanner", short: "RP" },
  { name: "coordinator", title: "Coordinator", short: "Co" },
];

// The stream's "agent" messages use the short Cominty agent keys.
export const AGENT_KEY: Record<string, AgentName> = {
  impact: "impact_analyst",
  finder: "resource_finder",
  replanner: "replanner",
  coordinator: "coordinator",
};

export function idleAgents(): Record<AgentName, AgentView> {
  const detail = "Asleep until something goes wrong";
  return {
    impact_analyst: { status: "idle", detail },
    resource_finder: { status: "idle", detail },
    replanner: { status: "idle", detail },
    coordinator: { status: "idle", detail },
  };
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Agent panel for a finished (saved) disruption. */
export function agentsFor(e: Partial<Entry>): Record<AgentName, AgentView> {
  const a = idleAgents();
  const r = e.routing;
  if (e.status === "questions") {
    a.impact_analyst = { status: "done", detail: "Asked you a question instead of guessing" };
    return a;
  }
  if (e.impact) {
    const n = e.impact.waves.flat().length;
    a.impact_analyst = { status: "done", detail: `Found ${plural(n, "affected part")} of the event` };
  }
  if (!r) return a;
  const other = (name: AgentName, done: string): AgentView => {
    if (r.skipped[name]) return { status: "skipped", detail: `Skipped: ${r.skipped[name]}` };
    if (r.cached[name]) return { status: "cached", detail: "Same input as last time: reused its answer" };
    if (r.run.includes(name)) return { status: "done", detail: done };
    return { status: "idle", detail: "" };
  };
  a.resource_finder = other("resource_finder", `Found ${plural(e.candidates?.length ?? 0, "venue option")}, availability to confirm`);
  a.replanner = other("replanner", `Prepared ${plural(e.scored?.length ?? 0, "option")}, ranked by Ripple`);
  a.coordinator = other("coordinator", `${plural(e.outbox?.length ?? 0, "message")} ready for your review`);
  if (e.coordinator === "waiting_for_choice") {
    a.coordinator = { status: "waiting", detail: "Writes the messages once you pick an option" };
  }
  if (e.coordinator === "question") {
    a.coordinator = { status: "waiting", detail: "Asked you one question before writing the messages" };
  }
  return a;
}

export function entryToView(e: Entry): View {
  return { ...e, agents: agentsFor(e) };
}

/** Apply one stream message to the live view. Returns a new object (React state). */
export function applyStream(view: View, type: string, data: Record<string, any>): View {
  const v: View = { ...view, agents: { ...view.agents } };
  const set = (name: AgentName, status: AgentView["status"], detail: string) => {
    v.agents[name] = { status, detail };
  };
  switch (type) {
    case "agent": {
      const name = AGENT_KEY[data.agent];
      if (!name) break;
      if (data.status === "running" && data.description) set(name, "working", data.description);
      else if (v.agents[name].status !== "working") set(name, "working", v.agents[name].detail || "Working…");
      break;
    }
    case "agent_started":
      set(data.agent, "working", "Starting…");
      break;
    case "cost": {
      const name = data.agent as AgentName;
      v.costs = { ...(v.costs ?? {}), [name]: data.cost };
      set(name, "done", v.agents[name].detail);
      break;
    }
    case "impact": {
      v.impact = { origin: data.origin, kind: data.kind, waves: data.waves, effects: data.effects, checked_ok: data.checked_ok };
      v.disruption = data.disruption;
      const n = data.waves.flat().length;
      set("impact_analyst", "done", `Found ${plural(n, "affected part")} of the event`);
      break;
    }
    case "routing": {
      v.routing = data as View["routing"];
      for (const { name } of AGENTS.slice(1)) {
        if (data.skipped[name]) set(name, "skipped", `Skipped: ${data.skipped[name]}`);
        else if (data.cached[name]) set(name, "cached", "Same input as last time: reused its answer");
        else if (data.run.includes(name)) set(name, "waiting", "Queued");
        else set(name, "idle", "");
      }
      break;
    }
    case "candidates":
      v.candidates = data.candidates;
      set("resource_finder", "done", `Found ${plural(data.candidates.length, "venue option")}, availability to confirm`);
      break;
    case "scenarios":
      v.scored = data.scored;
      set("replanner", "done", `Prepared ${plural(data.scored.length, "option")}, ranked by Ripple`);
      break;
    case "saved":
      v.saved = data.saved;
      v.saved_total = data.saved_total;
      break;
    case "outbox":
      v.outbox = data.outbox;
      v.files = data.files;
      v.coordinator = "done";
      set("coordinator", "done", `${plural(data.outbox.length, "message")} ready for your review`);
      break;
    case "questions":
      v.status = "questions";
      v.questions = data.questions;
      set("impact_analyst", "done", "Asked you a question instead of guessing");
      break;
    case "agent_failed": {
      const name = data.agent as AgentName;
      if (v.agents[name]) set(name, "failed", data.reason);
      for (const { name: other } of AGENTS) {   // agents that never started are not "waiting" any more
        if (other !== name && (v.agents[other].status === "waiting" || v.agents[other].status === "working")) {
          set(other, "idle", "Not started: an earlier step failed");
        }
      }
      break;
    }
    case "coordinator_question":
      v.coordinator = "question";
      v.coordinator_question = data.questions;
      set("coordinator", "waiting", "Asked you one question before writing the messages");
      break;
    case "done":
      if (data.coordinator === "waiting_for_choice") {
        v.coordinator = "waiting_for_choice";
        set("coordinator", "waiting", "Writes the messages once you pick an option");
      }
      break;
  }
  return v;
}

export function startView(id: string, text: string): View {
  const agents = idleAgents();
  agents.impact_analyst = { status: "working", detail: "Reading your message…" };
  for (const { name } of AGENTS.slice(1)) agents[name] = { status: "waiting", detail: "Waiting for the impact map" };
  return { id, text, agents };
}

// ---- small helpers shared by components ----

export function labelOf(model: EventModel, id: string): string {
  if (id === "grp_newcomers") return "Newcomers";
  return model.nodes.find((n) => n.id === id)?.label ?? id;
}

export function sizeOf(model: EventModel, id: string): number | undefined {
  const s = model.nodes.find((n) => n.id === id)?.attrs.size;
  return typeof s === "number" ? s : undefined;
}

export function money(v: string | number | null | undefined, digits = 4): string {
  if (v === null || v === undefined || v === "") return "—";
  return Number(v).toFixed(digits);
}

// ---- client-friendly summaries (all from API data) ----

export const AGENT_SUBTITLE: Record<AgentName, string> = {
  impact_analyst: "Understanding what broke",
  resource_finder: "Looking for alternatives",
  replanner: "Preparing options",
  coordinator: "Writing the messages",
};

/** "search_web: Search Paris venues" -> "Search Paris venues" (tool names mean nothing to organizers). */
export function plainStep(text: string): string {
  return text.replace(/^[a-z_]+:\s*/, "");
}

/** Groups that cover other groups: workshop participants are attendees, Speaker 2 is a speaker. */
function topLevel(model: EventModel, ids: string[]) {
  const nodes = ids.map((id) => model.nodes.find((n) => n.id === id)).filter((n): n is NonNullable<typeof n> => !!n);
  const size = (n: (typeof nodes)[number]) => (typeof n.attrs.size === "number" ? n.attrs.size : 0);
  const word = (n: (typeof nodes)[number]) => n.label.toLowerCase().split(/\s+/)[0].replace(/s$/, "");
  return nodes.filter((n) => !nodes.some((o) => o !== n && o.source && o.source === n.source && size(o) > size(n) &&
    // participants.csv lists every attendee, so any bigger group from it covers this one
    (o.source === "participants.csv" || word(o) === word(n))));
}

export interface PeopleSummary {
  count: number;
  groups: { label: string; size: number }[];
  pending: boolean;   // newcomers not counted yet (the Coordinator has not run)
}

export function peopleAffected(model: EventModel, v: View): PeopleSummary | null {
  if (!v.impact) return null;
  const grown = v.disruption?.kind === "resized" ? v.impact.origin : null;
  const ids = Object.keys(v.impact.effects).filter((id) => id !== grown &&
    model.nodes.find((n) => n.id === id)?.type === "group");
  const groups = topLevel(model, ids).map((n) => ({ label: n.label, size: typeof n.attrs.size === "number" ? n.attrs.size : 0 }));
  let pending = false;
  if (grown) {
    // after a late registration, only the newcomers are affected: counted from the update file
    const csv = v.files?.find((f) => f.kind === "csv");
    if (csv?.rows && v.outbox?.some((m) => m.group === "grp_newcomers")) groups.unshift({ label: "Newcomers", size: csv.rows });
    else pending = true;
  }
  return { count: groups.reduce((s, g) => s + g.size, 0), groups, pending };
}

/** Parts of the event the problem did not touch. */
export function unchanged(model: EventModel, v: View): string[] {
  if (!v.impact) return [];
  const touched = new Set(Object.keys(v.impact.effects));
  return model.nodes.filter((n) => !touched.has(n.id)).map((n) => n.label);
}

// ---- dates people read: "2026-11-21" / "Saturday 21 November 2026" -> "Sat 21 Nov" ----

const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];
const short = (d: Date) => d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

export function humanDates(text: string): string {
  return text
    .replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (m, y, mo, d) => {
      const dt = new Date(Number(y), Number(mo) - 1, Number(d));
      return Number.isNaN(dt.getTime()) ? m : short(dt);
    })
    .replace(/\b(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s+)?(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)(?:\s+(\d{4}))?\b/gi,
      (m, d, mon, y) => {
        const dt = new Date(y ? Number(y) : new Date().getFullYear(), MONTHS.indexOf(mon.toLowerCase()), Number(d));
        return Number.isNaN(dt.getTime()) ? m : short(dt);
      });
}
