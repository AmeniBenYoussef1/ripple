// App-wide state: the event from the API, the disruption running right now (live), the live
// feed and agent timings. Every number shown in the UI comes from here, i.e. from the API.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError, friendlyError, getEvent, setEvent } from "./api";
import type { AgentName, Entry, EventState, View } from "./types";
import { AGENT_KEY, AGENTS, applyStream, entryToView, plainStep, startView } from "./view";

const STREAM_TYPES = ["started", "agent", "agent_started", "cost", "impact", "routing", "candidates",
  "scenarios", "saved", "outbox", "questions", "coordinator_question", "agent_failed", "done", "error"];

export interface FeedItem {
  at: number | null;          // null = rebuilt from the saved entry (no live timestamp)
  kind: "step" | "finding" | "cost" | "draft" | "skip" | "error";
  text: string;
}

export type Timing = Partial<Record<AgentName, { start: number; end?: number }>>;

interface Store {
  state: EventState | null;
  view?: View;               // what the workspace shows: live run, or the selected entry
  selected?: string;
  select: (id: string) => void;
  busy: boolean;
  error: string | null;
  errorDetail: string | null;          // technical text, shown only in Behind the scenes
  clearError: () => void;
  notice: Notice | null;               // success messages
  clearNotice: () => void;
  pending: string | null;              // what is running: "report", "choose:B", "answer", "approve"
  feed: FeedItem[];
  timing: Timing;
  report: (text: string) => Promise<void>;
  choose: (scenarioId: string) => Promise<void>;
  answer: (text: string) => Promise<void>;
  toggleAction: (id: string, done: boolean) => Promise<void>;
  editMessage: (id: string, subject: string, body: string) => Promise<boolean>;
  decide: (kind: "changes_requested" | "rejected", option: string, note?: string) => Promise<void>;
  relax: (body: { budget_eur?: number; capacity_slack?: number }) => Promise<void>;
  replan: (note: string) => Promise<void>;
  approve: (ids: string[]) => Promise<void>;
  reset: () => Promise<void>;
  analyze: (files: File[]) => Promise<boolean>;
  confirmSetup: () => Promise<void>;
  eventId: string;
  events: EventSummary[];
  switchEvent: (id: string) => Promise<void>;
  createEvent: (id: string, title: string) => Promise<boolean>;
}

export interface EventSummary { id: string; title: string; problems: number; setup: string; replay: boolean }

export interface Notice { text: string; target?: "outbox" | "question" }

const Ctx = createContext<Store | null>(null);

export function useRipple(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useRipple outside RippleProvider");
  return s;
}

// Feed and timings are only known in the browser; keep them across reloads.
function load<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "") as T;
  } catch {
    return fallback;
  }
}

const title = (name: string) => AGENTS.find((a) => a.name === name)?.title ?? name;
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** Turn one stream message into a feed line (or nothing). */
function feedLine(type: string, d: Record<string, any>): FeedItem | null {
  const at = Date.now();
  switch (type) {
    case "agent":
      return d.event === "ToolCall" && d.status === "running" && d.description
        ? { at, kind: "step", text: `${title(AGENT_KEY[d.agent] ?? d.agent)}: ${plainStep(d.description)}` } : null;
    case "impact":
      return { at, kind: "finding", text: `The problem touches ${plural(d.waves.flat().length, "part")} of the event` };
    case "routing": {
      const skipped = Object.keys(d.skipped).map(title);
      return { at, kind: "skip", text: `Called in: ${d.run.map(title).join(", ") || "nobody"}${skipped.length ? `. Not needed: ${skipped.join(", ")}` : ""}` };
    }
    case "candidates":
      return { at, kind: "finding", text: `Found ${plural(d.candidates.length, "venue option")}, each with a link to its page` };
    case "scenarios": {
      const best = d.scored.find((s: any) => s.rank === 1);
      return { at, kind: "finding", text: `${plural(d.scored.length, "option")} ranked${best ? `; best: ${best.title}` : ""}` };
    }
    case "cost":   // costs live in "Behind the scenes", not in the organizer's feed
      return null;
    case "outbox":
      return { at, kind: "draft", text: `${plural(d.outbox.length, "message")} ready for your review` };
    case "questions":
      return { at, kind: "finding", text: "The Impact Analyst asked a question instead of guessing" };
    case "agent_failed":   // plain words only; the technical text is in Behind the scenes
      return { at, kind: "error", text: `${title(d.agent)} stopped: ${d.reason}` };
    case "error":
      return null;
    default:
      return null;
  }
}

/** Feed for an entry with no live record (e.g. after a server restart). */
export function summaryFeed(e: Entry): FeedItem[] {
  const out: FeedItem[] = [];
  const push = (kind: FeedItem["kind"], text: string) => out.push({ at: null, kind, text });
  if (e.impact) push("finding", `The problem touches ${plural(e.impact.waves.flat().length, "part")} of the event`);
  if (e.routing) {
    const skipped = Object.keys(e.routing.skipped).map(title);
    push("skip", `Called in: ${e.routing.run.map(title).join(", ")}${skipped.length ? `. Not needed: ${skipped.join(", ")}` : ""}`);
  }
  if (e.candidates?.length) push("finding", `Found ${plural(e.candidates.length, "venue option")}`);
  if (e.scored?.length) push("finding", `${plural(e.scored.length, "option")} ranked`);
  if (e.outbox?.length) push("draft", `${plural(e.outbox.length, "message")} ready for your review`);
  return out.reverse();
}

export function RippleProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<EventState | null>(null);
  const [selected, setSelected] = useState<string | undefined>();
  const [live, setLive] = useState<View | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const fail = useCallback((e: unknown) => {
    if (e instanceof ApiError) {
      setError(e.message);
      setErrorDetail(e.detail);
    } else {
      setError(friendlyError(500, ""));
      setErrorDetail(e instanceof Error ? e.message : String(e));
    }
  }, []);
  const [feeds, setFeeds] = useState<Record<string, FeedItem[]>>(() => load("ripple.feeds", {}));
  const [timings, setTimings] = useState<Record<string, Timing>>(() => load("ripple.timings", {}));
  const busyRef = useRef(false);
  const [eventId, setEventId] = useState(getEvent());
  const [events, setEvents] = useState<EventSummary[]>([]);
  // feeds and timings are kept per event: disruption ids (d1, d2...) repeat across events
  const fk = useCallback((id: string) => `${getEvent()}/${id}`, []);
  const liveId = useRef<string>("pending");

  useEffect(() => localStorage.setItem("ripple.feeds", JSON.stringify(feeds)), [feeds]);
  useEffect(() => localStorage.setItem("ripple.timings", JSON.stringify(timings)), [timings]);

  const refresh = useCallback(async () => {
    const s = await api.load();
    setEventId(getEvent());
    api.listEvents().then(setEvents).catch(() => undefined);
    setState(s);
    setSelected((cur) => (cur && s.history.some((h) => h.id === cur) ? cur : s.history.at(-1)?.id));
    return s;
  }, []);

  useEffect(() => {
    refresh().catch(fail);
  }, [refresh, fail]);

  // One SSE connection for the whole app; messages count only while a request runs.
  useEffect(() => {
    const es = new EventSource(api.streamUrl());
    const on = (type: string) => (msg: MessageEvent) => {
      if (!busyRef.current) return;
      const d = JSON.parse(msg.data);
      if (type === "started") liveId.current = d.disruption_id;
      const id = liveId.current;
      const line = feedLine(type, d);
      if (line) setFeeds((f) => ({ ...f, [fk(id)]: [line, ...(f[fk(id)] ?? [])].slice(0, 200) }));

      const agent: AgentName | undefined =
        type === "agent" ? AGENT_KEY[d.agent] : type === "agent_started" || type === "cost" ? d.agent : undefined;
      if (agent) {
        setTimings((t) => {
          const cur = { ...(t[fk(id)] ?? {}) };
          const now = Date.now();
          if (type === "cost") cur[agent] = { start: cur[agent]?.start ?? now, end: now };
          else if (!cur[agent] || cur[agent]!.end) cur[agent] = { start: now };
          return { ...t, [fk(id)]: cur };
        });
      }
      if (type === "cost") {
        setState((s) => s && { ...s, total_cost: d.total, costs: [...s.costs, { disruption: id, agent: d.agent, cost: d.cost }] });
      }
      if (type === "agent_failed") setErrorDetail(d.detail);
      if (type === "error") {
        setError(friendlyError(500, d.message));
        setErrorDetail(d.message);
        return;
      }
      setLive((v) => (type === "started" ? startView(d.disruption_id, d.text) : v ? applyStream(v, type, d) : v));
    };
    STREAM_TYPES.forEach((t) => es.addEventListener(t, on(t)));
    return () => es.close();
  }, [eventId]);

  const run = useCallback(async <T,>(what: string, work: () => Promise<T>, startLive?: View): Promise<T | null> => {
    setError(null);
    setNotice(null);
    setPending(what);
    setBusy(true);
    let result: T | null = null;
    let failed = false;
    busyRef.current = true;
    if (startLive) {
      liveId.current = startLive.id;
      setLive(startLive);
    }
    try {
      result = await work();
    } catch (e) {
      failed = true;
      fail(e);
    } finally {
      busyRef.current = false;
      setBusy(false);
      setPending(null);
      try {
        const s = await refresh();
        const reopened = (result as { id?: string; reopened?: boolean } | null)?.reopened;
        if (startLive) {
          setSelected(reopened ? (result as { id: string }).id
            : startLive.id === "pending" ? s.history.at(-1)?.id : startLive.id);
        }
      } catch (e) {
        fail(e);
      }
      // after a failure, keep the run on screen so the organizer sees which agent failed and why
      if (!failed) setLive(null);
    }
    return result;
  }, [refresh, fail]);

  const entry = state?.history.find((h) => h.id === selected);
  const view = live ?? (entry ? entryToView(entry) : undefined);
  const feed = view ? feeds[fk(view.id)] ?? (entry ? summaryFeed(entry) : []) : [];
  const timing = view ? timings[fk(view.id)] ?? {} : {};

  const store: Store = useMemo(() => ({
    state, view, selected, busy, error, errorDetail, feed, timing, notice, pending,
    select: (id: string) => { setLive(null); setSelected(id); },
    clearError: () => setError(null),
    clearNotice: () => setNotice(null),
    report: async (text: string) => {
      const r = await run("report", () => api.disrupt(text), startView("pending", text));
      if (r?.reopened) setNotice({ text: "You already reported this problem, so Ripple reopened it instead of starting over." });
    },
    choose: async (sid: string) => {
      if (!view) return;
      const base: View = { ...view, chosen: sid, agents: { ...view.agents, coordinator: { status: "working", detail: "Starting…" } } };
      const r = await run(`choose:${sid}`, () => api.choose(sid), base);
      if (r?.status === "question") setNotice({ text: `Option ${sid} approved. The Coordinator has one question before writing the messages.`, target: "question" });
      else if (r) setNotice({ text: `Option ${sid} approved. ${r.outbox.length} messages are ready for your review.`, target: "outbox" });
    },
    answer: async (text: string) => {
      if (!view) return;
      const base: View = { ...view, agents: { ...view.agents, coordinator: { status: "working", detail: "Reading your answer…" } } };
      const r = await run("answer", () => api.answer(text, view.id), base);
      if (r?.status === "question") setNotice({ text: "The Coordinator needs one more answer.", target: "question" });
      else if (r) setNotice({ text: `Thanks. ${r.outbox.length} messages are ready for your review.`, target: "outbox" });
    },
    toggleAction: async (id: string, done: boolean) => {
      // small code-only change: update on screen at once, then confirm with the server
      setState((s) => s && { ...s, history: s.history.map((h) => ({ ...h,
        actions: h.actions?.map((a) => (a.id === id ? { ...a, status: done ? "done" : "open" } : a)) })) });
      try {
        await api.setAction(id, done);
        await refresh();
      } catch (e) {
        fail(e);
        await refresh().catch(() => undefined);
      }
    },
    editMessage: async (id: string, subject: string, body: string) => {
      try {
        await api.editMessage(id, subject, body);
        await refresh();
        setNotice({ text: "Draft updated. The earlier version is kept in its history." });
        return true;
      } catch (e) {
        fail(e);
        return false;
      }
    },
    decide: async (kind, option, note = "") => {
      if (!view) return;
      try {
        await api.decide(view.id, kind, option, note);
        await refresh();
        setNotice({ text: kind === "rejected" ? `Option ${option} rejected. Ripple shows the next best option.`
          : `Your request for option ${option} is saved. Choose another option or report a new problem.` });
      } catch (e) {
        fail(e);
      }
    },
    relax: async (body) => {
      if (!view) return;
      try {
        const r = await api.relax(view.id, body);
        await refresh();
        const n = r.scored.filter((x) => x.feasible).length;
        setNotice({ text: n ? `With this change, ${n} option${n === 1 ? "" : "s"} now fit${n === 1 ? "s" : ""} your limits.`
          : "Still no option fits. Try relaxing another limit." });
      } catch (e) {
        fail(e);
      }
    },
    replan: async (note: string) => {
      if (!view) return;
      const base: View = { ...view, agents: { ...view.agents, replanner: { status: "working", detail: "Preparing new options…" } } };
      const r = await run("replan", () => api.replan(view.id, note), base);
      if (r) setNotice({ text: `The Replanner prepared ${r.scored.length} new options.` });
    },
    approve: async (ids: string[]) => {
      const r = await run("approve", () => api.approve(ids));
      if (r) setNotice({ text: `${r.approved.length} message${r.approved.length === 1 ? "" : "s"} approved. Nothing has been sent: copy or export them when you are ready.` });
    },
    analyze: async (files: File[]) => {
      // own flow: the agent stream of a setup is shown by the setup page, not the workspace
      setError(null);
      setBusy(true);
      try {
        const r = await api.analyze(files);
        setState(r.state);
        setSelected(undefined);
        if (r.status === "questions") setError("The Impact Analyst needs more information: " + (r.questions ?? []).map((q) => q.prompt).join(" "));
        return r.status === "analyzed";
      } catch (e) {
        fail(e);
        return false;
      } finally {
        setBusy(false);
      }
    },
    confirmSetup: async () => {
      try {
        setState(await api.confirmSetup());
      } catch (e) {
        fail(e);
      }
    },
    reset: async () => {
      await run("reset", () => api.reset());
      const mine = (o: Record<string, unknown>) => Object.fromEntries(Object.entries(o).filter(([k]) => !k.startsWith(`${getEvent()}/`)));
      setFeeds((f) => mine(f) as Record<string, FeedItem[]>);
      setTimings((t) => mine(t) as Record<string, Timing>);
    },
    eventId, events,
    switchEvent: async (id: string) => {
      if (id === getEvent()) return;
      setEvent(id);
      setEventId(id);
      setLive(null);
      setSelected(undefined);
      setNotice(null);
      setError(null);
      setState(null);
      try {
        await refresh();
      } catch (e) {
        fail(e);
      }
    },
    createEvent: async (id: string, title: string) => {
      try {
        await api.createEvent(id, title);
        setEvent(id);
        setEventId(id);
        setLive(null);
        setSelected(undefined);
        await refresh();
        setNotice({ text: `Event "${title}" created. Add its files and let Ripple analyze them.` });
        return true;
      } catch (e) {
        fail(e);
        return false;
      }
    },
  }), [state, view, selected, busy, error, errorDetail, feed, timing, notice, pending, run, fail, eventId, events, refresh]);

  return <Ctx.Provider value={store}>{children}</Ctx.Provider>;
}
