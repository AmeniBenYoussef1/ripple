// Small client for the Ripple API. Errors become friendly messages for the UI.
import type { EventState, Message, Venues } from "./types";

export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
export const EVENT_ID = "demo";

/** message = what the organizer sees; detail = the technical text (shown in Behind the scenes). */
export class ApiError extends Error {
  detail: string;
  constructor(message: string, detail = "") {
    super(message);
    this.detail = detail || message;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      ...init,
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
    throw new ApiError(friendlyError(res.status, detail), `${res.status} ${path}: ${detail}`);
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
  if (/at most \d+ files|only CSV|10 MB|is empty|add your event files|write an answer/.test(detail)) {
    return detail.charAt(0).toUpperCase() + detail.slice(1) + ".";
  }
  return "Something went wrong. The details are in Behind the scenes.";
}

const ev = `/api/events/${EVENT_ID}`;

export const api = {
  async load(): Promise<EventState> {
    try {
      return await call<EventState>(ev);
    } catch (e) {
      if (e instanceof ApiError && e.message.startsWith("unknown event")) return api.setup();
      throw e;
    }
  },
  setup: () => call<EventState>(`${ev}/setup`, { method: "POST", body: "{}" }),
  reset: () => call<EventState>(`${ev}/reset`, { method: "POST" }),
  disrupt: (text: string) => call<{ id: string; reopened?: boolean }>(`${ev}/disruptions`, { method: "POST", body: JSON.stringify({ text }) }),
  choose: (sid: string) => call<{ status: string; outbox: Message[] }>(`${ev}/scenarios/${encodeURIComponent(sid)}/choose`, { method: "POST" }),
  approve: (ids: string[]) =>
    call<{ approved: Message[] }>(`${ev}/outbox/approve`, { method: "POST", body: JSON.stringify({ ids }) }),
  venues: (disruptionId: string) => call<Venues>(`${ev}/venues?disruption=${encodeURIComponent(disruptionId)}`),
  /** Multipart upload, so no JSON content-type here. */
  async analyze(files: File[]): Promise<{ status: string; state: EventState; questions?: { prompt: string; options: string[] }[] }> {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    let res: Response;
    try {
      res = await fetch(`${API}${ev}/analyze`, { method: "POST", body: form });
    } catch {
      throw new ApiError(friendlyError(0, ""), `analyze: server unreachable at ${API}`);
    }
    const body = await res.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`;
    if (!res.ok) throw new ApiError(friendlyError(res.status, detail), `${res.status} analyze: ${detail}`);
    return body;
  },
  answer: (text: string, disruption?: string) =>
    call<{ status: string; outbox: Message[] }>(`${ev}/answer`, { method: "POST", body: JSON.stringify({ text, disruption }) }),
  confirmSetup: () => call<EventState>(`${ev}/setup/confirm`, { method: "POST" }),
  demoFiles: () => call<{ name: string; bytes: number }[]>("/api/demo-files"),
  demoFileUrl: (name: string) => `${API}/api/demo-files/${encodeURIComponent(name)}`,
  fileUrl: (name: string) => `${API}${ev}/files/${encodeURIComponent(name)}`,
  streamUrl: () => `${API}${ev}/stream`,
};
