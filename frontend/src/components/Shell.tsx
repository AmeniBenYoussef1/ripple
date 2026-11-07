// Sidebar, the stats bar and the "Report a problem" dialog.
import { FileUp, FlaskConical, History, Home, Inbox, LayoutDashboard, Settings, X, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { ThemeToggle } from "../theme";
import { useRipple } from "../store";
import { money } from "../view";

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 30 30" fill="none" aria-hidden="true">
      <circle cx="15" cy="15" r="4" fill="#C14364" />
      <circle cx="15" cy="15" r="8.5" stroke="#FF916B" strokeWidth="2" />
      <circle cx="15" cy="15" r="13" stroke="#FF916B" strokeWidth="1.5" strokeOpacity="0.55" />
    </svg>
  );
}

function EventSwitcher() {
  const { events, eventId, switchEvent, busy } = useRipple();
  const nav = useNavigate();
  const list = events.some((e) => e.id === eventId) ? events : [...events, { id: eventId, title: eventId, problems: 0, setup: "", replay: false }];
  return (
    <label className="event-switch">
      <span className="eyebrow">Event</span>
      <select className="text-input" value={eventId} disabled={busy} aria-label="Event"
        onChange={async (e) => {
          const v = e.target.value;
          if (v === "__new") { nav("/app/setup?new=1"); return; }
          await switchEvent(v);
          const ev = events.find((x) => x.id === v);
          nav(ev && ev.setup !== "analyzed" ? "/app/setup" : "/app/event");
        }}>
        {list.map((e) => <option key={e.id} value={e.id}>{e.title}{e.replay ? " (Replay)" : ""}</option>)}
        <option value="__new">+ New event…</option>
      </select>
    </label>
  );
}

export function Sidebar() {
  const { state } = useRipple();
  const drafts = state?.history.reduce((n, h) => n + (h.outbox ?? []).filter((m) => m.status === "draft").length, 0) ?? 0;
  const links = [
    { to: "/app", label: "Home", icon: <Home size={18} />, end: true },
    { to: "/app/setup", label: "Event setup", icon: <FileUp size={18} />, badge: state?.setup?.confirmed ? undefined : ("!" as const) },
    { to: "/app/event", label: "Event workspace", icon: <LayoutDashboard size={18} /> },
    { to: "/app/whatif", label: "What-if", icon: <FlaskConical size={18} /> },
    { to: "/app/outbox", label: "Outbox", icon: <Inbox size={18} />, badge: drafts || undefined },
    { to: "/app/history", label: "History", icon: <History size={18} /> },
    { to: "/app/settings", label: "Settings", icon: <Settings size={18} /> },
  ];
  return (
    <div className="side-col"><nav className="sidebar" aria-label="Main">
      <div className="side-brand"><Logo /><span>Ripple</span><span className="side-theme"><ThemeToggle /></span></div>
      <EventSwitcher />
      <ul className="side-links">
        {links.map((l) => (
          <li key={l.to}>
            <NavLink to={l.to} end={l.end} className={({ isActive }) => `side-link ${isActive ? "active" : ""}`}>
              {l.icon}<span>{l.label}</span>
              {l.badge ? <span className="side-badge" aria-label={l.badge === "!" ? "needs checking" : `${l.badge} drafts`}>{l.badge}</span> : null}
            </NavLink>
          </li>
        ))}
      </ul>
      <div className="side-user">
        <span className="avatar" aria-hidden="true">A</span>
        <div><div className="user-name">Ameni</div><div className="user-role">Organizer</div></div>
      </div>
    </nav></div>
  );
}

export function StatsBar() {
  const { state } = useRipple();
  if (!state) return null;
  const skipped = state.history.reduce(
    (s, h) => s + Object.keys(h.routing?.skipped ?? {}).length + Object.keys(h.routing?.cached ?? {}).length, 0);
  const est = state.history.some((h) => h.saved_is_estimate);
  return (
    <div className="stats">
      <Stat label="Agent runs" value={String(state.costs.length)} />
      <Stat label="Skipped" value={String(skipped)} />
      <Stat label="Cost" value={money(state.total_cost)} title="Sum of Cominty Result cost.total" />
      <Stat label="≈ Saved" value={`${money(state.saved_total)}${est ? " est." : ""}`}
        title={est ? "Partly estimated from recorded runs" : "Average real cost of the agents that slept"} />
      <div className="mode" role="group" aria-label="Agent mode"
        title="Set by DEMO_MODE in backend/.env when the server starts">
        <span className={`mode-opt ${state.demo_mode ? "" : "on"}`}>Live</span>
        <span className={`mode-opt ${state.demo_mode ? "on" : ""}`}>Replay</span>
      </div>
    </div>
  );
}

function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="stat" title={title}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}

export const DEMOS = ["The hall just cancelled.", "Speaker 2's train is 2 hours late.", "30 late registrations."];

/** "Report a problem": free text or one of the three demo sentences. */
export function ReportDialog({ onClose }: { onClose: () => void }) {
  const { report, busy } = useRipple();
  const nav = useNavigate();
  const ref = useRef<HTMLDialogElement>(null);
  const [text, setText] = useState("");
  useEffect(() => ref.current?.showModal(), []);
  const send = (t: string) => {
    if (!t.trim() || busy) return;
    ref.current?.close();
    nav("/app/event");
    void report(t.trim());
  };
  return (
    <dialog ref={ref} className="dialog" onClose={onClose} aria-labelledby="report-title">
      <div className="dialog-head">
        <h2 id="report-title">What went wrong?</h2>
        <button type="button" className="icon-btn" aria-label="Close" onClick={() => ref.current?.close()}><X size={18} /></button>
      </div>
      <p className="sub">Describe it in your own words. Ripple finds what it touches and wakes only the agents it needs.</p>
      <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. The hall just cancelled."
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} autoFocus />
      <div className="quick">
        <span className="eyebrow">Quick demo</span>
        {DEMOS.map((d, i) => (
          <button key={d} type="button" className="quick-btn" onClick={() => send(d)} disabled={busy}>
            <span className="quick-n">{i + 1}</span>{d}
          </button>
        ))}
      </div>
      <div className="row-end">
        <button type="button" className="btn btn-ghost" onClick={() => ref.current?.close()}>Cancel</button>
        <button type="button" className="btn btn-primary" onClick={() => send(text)} disabled={busy || !text.trim()}>
          <Zap size={16} />Drop it in
        </button>
      </div>
    </dialog>
  );
}

export function useReport() {
  const [open, setOpen] = useState(false);
  return { open, show: () => setOpen(true), dialog: open ? <ReportDialog onClose={() => setOpen(false)} /> : null };
}
