// Small client for the Ripple API. Errors become friendly messages for the UI.
import type { EventState, Message, Venues } from "./types";

export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
/** The event on screen. "demo" is the CodeSprint demo (always replayed); others are your own events. */
export const DEMO_EVENT = "demo";
let currentEvent = localStorage.getItem("ripple.event") || DEMO_EVENT;
export const getEvent = () => currentEvent;
export function setEvent(id: string) {
  currentEvent = id;
  localStorage.setItem("ripple.event", id);
}

/** message = what the organizer sees; detail = the technical text (shown in Behind the scenes). */
export class ApiError extends Error {
  detail: string;
  /** "limit", "live-off", "public", "not-event", "vague"... when the server said why */
  kind: string;
  constructor(message: string, detail = "", kind = "") {
    super(message);
    this.detail = detail || message;
    this.kind = kind;
  }
}

/** The server prefixes plain-language refusals with their kind: "limit|You've used today's…". */
const KINDS = ["limit", "live-off", "public", "not-event", "vague"];
function kindOf(detail: string): [string, string] {
  const i = detail.indexOf("|");
  const k = i > 0 ? detail.slice(0, i) : "";
  return KINDS.includes(k) ? [k, detail.slice(i + 1)] : ["", detail];
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      ...init,
      credentials: "include",   // the visitor cookie (public Start flow)
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(friendlyError(0, ""), `fetch ${path} failed: server unreachable at ${API}`);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* not JSON */
    }
    const [kind, text] = kindOf(detail);
    throw new ApiError(kind ? text : friendlyError(res.status, detail), `${res.status} ${path}: ${detail}`, kind);
  }
  return res.json() as Promise<T>;
}

/** Plain words for the organizer; never a raw technical error. */
export function friendlyError(status: number, detail: string): string {
  if (status === 0) return "Ripple's server can't be reached. Check that it is running, then try again.";
  if (detail.includes("no recording")) return "This step wasn't recorded, so it is available in Live mode only.";
  if (status === 409) return "Ripple is still working on the last request. Give it a moment.";
  if (status === 429) return "The AI platform is busy right now. Try again in a minute.";
  if (status === 502) return "An agent couldn't finish this step. Please try again.";
  if (detail.includes("already chosen")) return "You already picked an option for this problem.";
  if (detail.includes("fails a hard limit")) return "This option breaks one of your limits, so it can't be approved.";
  if (detail.includes("no question")) return "There is no open question to answer.";
  if (detail.includes("unknown event")) return "This event could not be found. Reload the page.";
  if (detail.includes("unknown message ids")) return "Some of these messages no longer exist. Reload the page.";
  // checks written in plain words on the server: show them as they are
  const [kind, text] = kindOf(detail);
  if (kind) return text;
  if (/at most \d+ files|only CSV|\d+ MB|is empty|write what went wrong|add your event files|write an answer|cannot be negative|must be positive|pick a session|a delay is between|has no budget|only drafts can be edited|needs a subject|say which limit|was rejected|no options to re-plan/.test(detail)) {
    return detail.charAt(0).toUpperCase() + detail.slice(1) + ".";
  }
  return "Something went wrong. The details are in Behind the scenes.";
}

const ev = () => `/api/events/${encodeURIComponent(currentEvent)}`;

export interface WhatIfRipple {
  groups: { id: string; label: string; size?: number | null }[];
  parts: { id: string; label: string; effect: string }[];
  run: string[];
  skipped: Record<string, string>;
  cached: string[];
}
export interface WhatIfRow { rank: number | null; verdict: string; violations: string[] }
export interface WhatIf {
  label: string;
  budget: { now: number | null; whatif: number | null };
  attendees: { now: number | null; whatif: number | null; ripple: WhatIfRipple | null };
  delay: { node: string; label: string; minutes: number; ripple: WhatIfRipple } | null;
  options: { problem: string; text: string; rows: { id: string; title: string; now: WhatIfRow; whatif: WhatIfRow }[] } | null;
  apply: { kind: "budget" | "problem"; text: string; value?: number; replayable: boolean }[];
}

export const api = {
  async load(): Promise<EventState> {
    try {
      return await call<EventState>(ev());
    } catch (e) {
      // an event that no longer exists: go back to the CodeSprint demo
      if (e instanceof ApiError && e.detail.includes("unknown event")) {
        if (currentEvent !== DEMO_EVENT) {
          setEvent(DEMO_EVENT);
          return api.load();
        }
        return api.setup();
      }
      throw e;
    }
  },
  setup: () => call<EventState>(`${ev()}/setup`, { method: "POST", body: "{}" }),
  reset: () => call<EventState>(`${ev()}/reset`, { method: "POST" }),
  disrupt: (text: string) => call<{ id: string; reopened?: boolean }>(`${ev()}/disruptions`, { method: "POST", body: JSON.stringify({ text }) }),
  choose: (sid: string) => call<{ status: string; outbox: Message[] }>(`${ev()}/scenarios/${encodeURIComponent(sid)}/choose`, { method: "POST" }),
  approve: (ids: string[]) =>
    call<{ approved: Message[] }>(`${ev()}/outbox/approve`, { method: "POST", body: JSON.stringify({ ids }) }),
  venues: (disruptionId: string) => call<Venues>(`${ev()}/venues?disruption=${encodeURIComponent(disruptionId)}`),
  /** Multipart upload, so no JSON content-type here. */
  async analyze(files: File[]): Promise<{ status: string; state: EventState; questions?: { prompt: string; options: string[] }[] }> {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    let res: Response;
    try {
      res = await fetch(`${API}${ev()}/analyze`, { method: "POST", body: form, credentials: "include" });
    } catch {
      throw new ApiError(friendlyError(0, ""), `analyze: server unreachable at ${API}`);
    }
    const body = await res.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`;
    if (!res.ok) throw new ApiError(friendlyError(res.status, detail), `${res.status} analyze: ${detail}`, kindOf(detail)[0]);
    return body;
  },
  answer: (text: string, disruption?: string) =>
    call<{ status: string; outbox: Message[] }>(`${ev()}/answer`, { method: "POST", body: JSON.stringify({ text, disruption }) }),
  setAction: (id: string, done: boolean) =>
    call<unknown>(`${ev()}/actions/${encodeURIComponent(id)}`, { method: "POST", body: JSON.stringify({ done }) }),
  decide: (did: string, kind: "changes_requested" | "rejected", option: string, note = "") =>
    call<unknown>(`${ev()}/problems/${encodeURIComponent(did)}/decision`, { method: "POST", body: JSON.stringify({ kind, option, note }) }),
  relax: (did: string, body: { budget_eur?: number; capacity_slack?: number }) =>
    call<{ scored: { feasible: boolean }[] }>(`${ev()}/problems/${encodeURIComponent(did)}/relax`, { method: "POST", body: JSON.stringify(body) }),
  replan: (did: string, note: string) =>
    call<{ scored: { feasible: boolean }[] }>(`${ev()}/problems/${encodeURIComponent(did)}/replan`, { method: "POST", body: JSON.stringify({ note }) }),
  editMessage: (id: string, subject: string, body: string) =>
    call<Message>(`${ev()}/outbox/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ subject, body }) }),
  whatif: (body: { budget_eur?: number; attendees?: number; delay_node?: string; delay_minutes?: number }) =>
    call<WhatIf>(`${ev()}/whatif`, { method: "POST", body: JSON.stringify(body) }),
  setBudget: (remaining_eur: number) =>
    call<EventState>(`${ev()}/budget`, { method: "POST", body: JSON.stringify({ remaining_eur }) }),
  listEvents: () => call<{ id: string; title: string; problems: number; setup: string; replay: boolean }[]>("/api/events"),
  createEvent: (id: string, title: string) =>
    call<EventState>(`/api/events/${encodeURIComponent(id)}/create`, { method: "POST", body: JSON.stringify({ title }) }),
  visitorStatus: () => call<VisitorStatus>("/api/visitor/status"),
  ownerToken: (token: string) => call<{ owner: boolean }>("/api/visitor/owner", { method: "POST", body: JSON.stringify({ token }) }),
  visitorEvent: (title: string) => call<EventState>("/api/visitor/event", { method: "POST", body: JSON.stringify({ title }) }),
  visitorSample: () => call<EventState>("/api/visitor/sample", { method: "POST" }),
  samplePackUrl: () => `${API}/api/sample-pack.zip`,
  confirmSetup: () => call<EventState>(`${ev()}/setup/confirm`, { method: "POST" }),
  demoFiles: () => call<{ name: string; bytes: number }[]>("/api/demo-files"),
  demoFileUrl: (name: string) => `${API}/api/demo-files/${encodeURIComponent(name)}`,
  fileUrl: (name: string) => `${API}${ev()}/files/${encodeURIComponent(name)}`,
  streamUrl: () => `${API}${ev()}/stream`,
};

export interface VisitorStatus {
  public: boolean;
  live: boolean;            // Live agents available on this server
  analyses_left: number | null;   // null: no visitor limit applies
  problems_left: number | null;
  owner?: boolean;
  budget_left: boolean;
  max_file_mb: number;
  ttl_hours: number;
  events: string[];
}

/** A problem to report as soon as the workspace opens (set by the Start page). */
export const PENDING = "ripple.pendingProblem";
