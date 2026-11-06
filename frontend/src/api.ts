// Small client for the Ripple API. Errors become friendly messages for the UI.
import type { EventState, Message, Venues } from "./types";

export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
export const EVENT_ID = "demo";

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(`Can't reach the Ripple server at ${API}. Is the backend running?`);
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
    throw new ApiError(friendly(res.status, detail));
  }
  return res.json() as Promise<T>;
}

function friendly(status: number, detail: string): string {
  if (status === 409) return "Ripple is still working on the last request. Give it a moment.";
  if (status === 429) return "The AI platform is rate-limiting us. Try again in a minute.";
  if (status === 502) return `An agent couldn't finish: ${detail}`;
  if (detail.includes("no recording")) {
    return "Demo mode only knows the three demo problems. Pick one of the quick-demo buttons.";
  }
  return detail;
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
  disrupt: (text: string) => call<unknown>(`${ev}/disruptions`, { method: "POST", body: JSON.stringify({ text }) }),
  choose: (sid: string) => call<unknown>(`${ev}/scenarios/${encodeURIComponent(sid)}/choose`, { method: "POST" }),
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
      throw new ApiError(`Can't reach the Ripple server at ${API}. Is the backend running?`);
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(friendly(res.status, typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`));
    return body;
  },
  confirmSetup: () => call<EventState>(`${ev}/setup/confirm`, { method: "POST" }),
  demoFiles: () => call<{ name: string; bytes: number }[]>("/api/demo-files"),
  demoFileUrl: (name: string) => `${API}/api/demo-files/${encodeURIComponent(name)}`,
  fileUrl: (name: string) => `${API}${ev}/files/${encodeURIComponent(name)}`,
  streamUrl: () => `${API}${ev}/stream`,
};
