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
    a.impact_analyst = { status: "done", detail: `Traced ${plural(n, "affected item")} in ${plural(e.impact.waves.length, "wave")}` };
  }
  if (!r) return a;
  const other = (name: AgentName, done: string): AgentView => {
    if (r.skipped[name]) return { status: "skipped", detail: `Skipped: ${r.skipped[name]}` };
    if (r.cached[name]) return { status: "cached", detail: "Same input as last time: reused its answer" };
    if (r.run.includes(name)) return { status: "done", detail: done };
    return { status: "idle", detail: "" };
  };
  a.resource_finder = other("resource_finder", `Found ${plural(e.candidates?.length ?? 0, "candidate")}, availability to confirm`);
  a.replanner = other("replanner", `Proposed ${plural(e.scored?.length ?? 0, "scenario")}, scored by code`);
  a.coordinator = other("coordinator", `${plural(e.outbox?.length ?? 0, "draft")} ready for your review`);
  if (e.coordinator === "waiting_for_choice") {
    a.coordinator = { status: "waiting", detail: "Drafts ready once you pick a scenario" };
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
      set("impact_analyst", "done", `Traced ${plural(n, "affected item")} in ${plural(data.waves.length, "wave")}`);
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
      set("resource_finder", "done", `Found ${plural(data.candidates.length, "candidate")}, availability to confirm`);
      break;
    case "scenarios":
      v.scored = data.scored;
      set("replanner", "done", `Proposed ${plural(data.scored.length, "scenario")}, scored by code`);
      break;
    case "saved":
      v.saved = data.saved;
      v.saved_total = data.saved_total;
      break;
    case "outbox":
      v.outbox = data.outbox;
      v.files = data.files;
      v.coordinator = "done";
      set("coordinator", "done", `${plural(data.outbox.length, "draft")} ready for your review`);
      break;
    case "questions":
      v.status = "questions";
      v.questions = data.questions;
      set("impact_analyst", "done", "Asked you a question instead of guessing");
      break;
    case "done":
      if (data.coordinator === "waiting_for_choice") {
        v.coordinator = "waiting_for_choice";
        set("coordinator", "waiting", "Drafts ready once you pick a scenario");
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
