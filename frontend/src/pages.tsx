// The four pages: Home, Event workspace, Outbox, Settings.
import { CalendarDays, ChevronRight, Inbox, MapPin, Plus, RotateCcw, Server, Sun, Users } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { API, getEvent } from "./api";
import { AgentsCard, LiveFeed, OutboxCard, PlanCard, RecoveryPlan } from "./components/cards";
import { useReport } from "./components/Shell";
import { BehindTheScenes, ProblemSummary } from "./components/summary";
import { VenueCard } from "./components/VenueMap";
import { CardHead, Pill, Stepper, phaseOf, statusText } from "./components/ui";
import { RippleMap } from "./RippleMap";
import { useRipple } from "./store";
import { useTheme } from "./theme";
import type { EventModel } from "./types";
import { entryToView, peopleAffected } from "./view";

function eventFacts(m: EventModel) {
  const date = m.nodes.map((n) => n.attrs.date).find(Boolean) as string | undefined;
  const attendees = m.nodes.find((n) => n.type === "group" && n.source === "participants.csv" && !/workshop/i.test(n.label));
  const venue = m.nodes.find((n) => n.type === "resource" && typeof n.attrs.capacity === "number");
  return {
    date: date ? new Date(date).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }) : null,
    people: typeof attendees?.attrs.size === "number" ? attendees.attrs.size : null,
    venue: venue ? String(venue.attrs.replaced_by ?? venue.label) : null,
  };
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

// ---- Home ---------------------------------------------------------------------------

export function HomePage() {
  const { state, busy, select } = useRipple();
  const nav = useNavigate();
  const rep = useReport();
  if (!state) return null;
  const f = eventFacts(state.model);
  const last = state.history.at(-1);
  const status = last ? statusText(entryToView(last), busy) : { text: "All calm", tone: "ok" as const };
  const drafts = state.history.reduce((n, h) => n + (h.outbox ?? []).filter((m) => m.status === "draft").length, 0);
  return (
    <div className="page">
      <section className="banner">
        <svg className="banner-wave" viewBox="0 0 600 200" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient id="wave" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0" style={{ stopColor: "var(--coral)", stopOpacity: 0.28 }} />
              <stop offset="1" style={{ stopColor: "var(--cream)", stopOpacity: 0.6 }} />
            </linearGradient>
          </defs>
          <path d="M0 120 C 120 70, 220 170, 340 120 S 520 60, 600 100 L600 200 L0 200 Z" fill="url(#wave)" />
          <path d="M0 150 C 140 110, 260 190, 380 150 S 540 110, 600 140 L600 200 L0 200 Z" fill="url(#wave)" />
        </svg>
        <div className="banner-text">
          <h1>{greeting()}, Ameni</h1>
          <p>Drop one problem. Watch the ripple.</p>
          <button type="button" className="btn btn-primary btn-lg" onClick={rep.show} disabled={busy}><Plus size={18} />Report a problem</button>
        </div>
      </section>

      <div className="home-grid">
        <section className="card event-card">
          <CardHead icon={<CalendarDays size={18} />} title={state.model.title} right={<Pill tone={status.tone}>{status.text}</Pill>} />
          <ul className="facts">
            {f.date && <li><CalendarDays size={16} />{f.date}</li>}
            {f.people !== null && <li><Users size={16} />{f.people} participants</li>}
            {f.venue && <li><MapPin size={16} />{f.venue}</li>}
          </ul>
          <div className="facts-row">
            <div><span className="big">{state.history.length}</span><span className="sub">problems today</span></div>
            <div><span className="big">{drafts}</span><span className="sub">drafts to review</span></div>
          </div>
          <button type="button" className="btn btn-secondary" onClick={() => nav("/app/event")}>Open workspace<ChevronRight size={16} /></button>
        </section>

        <section className="card">
          <CardHead icon={<RotateCcw size={18} />} title="Problem history" sub="Everything Ripple handled today, newest first" />
          {state.history.length === 0 ? (
            <p className="empty">Nothing has gone wrong yet. When it does, report it and Ripple traces what it touches.</p>
          ) : (
            <ul className="hist">
              {[...state.history].reverse().map((h) => {
                const st = statusText(entryToView(h), false);
                const n = state.history.indexOf(h) + 1;
                return (
                  <li key={h.id}>
                    <button type="button" className="hist-row" onClick={() => { select(h.id); nav("/app/event"); }}>
                      <span className="hist-n">{n}</span>
                      <span className="hist-main">
                        <span className="hist-text">{h.text}</span>
                        <span className="sub">
                          {new Date(h.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
                          {(() => { const p = peopleAffected(state.model, entryToView(h)); return p ? ` · ${p.count}${p.pending ? "+" : ""} people affected` : ""; })()}
                        </span>
                      </span>
                      <Pill tone={st.tone}>{st.text}</Pill>
                      <ChevronRight size={16} className="muted" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
      {rep.dialog}
    </div>
  );
}

// ---- Workspace -----------------------------------------------------------------------

export function WorkspacePage() {
  const { state, view, busy, feed, select, selected } = useRipple();
  const rep = useReport();
  if (!state) return null;
  const n = view ? state.history.findIndex((h) => h.id === view.id) : -1;
  const number = n >= 0 ? n + 1 : state.history.length + 1;
  const st = view ? statusText(view, busy) : null;
  return (
    <div className="page">
      <header className="ws-head">
        <div className="ws-title">
          <div className="row wrap">
            {view ? <Pill tone="alert">Problem {number}</Pill> : <Pill tone="ok">No problem reported</Pill>}
            {st && <Pill tone={st.tone}>{st.text}</Pill>}
            {(() => {
              const mine = (state.plans ?? []).filter((p) => p.disruption === view?.id);
              return mine.length ? <Pill tone="info">Plan v{mine[mine.length - 1].version}</Pill> : null;
            })()}
          </div>
          <h1>{view ? view.text : "Event workspace"}</h1>
          {view?.disruption?.detail && <p className="sub">{view.disruption.detail}</p>}
        </div>
        <div className="ws-actions">
          {state.history.length > 1 && (
            <div className="seg" role="tablist" aria-label="Problems">
              {state.history.map((h, i) => (
                <button key={h.id} type="button" role="tab" aria-selected={!busy && selected === h.id}
                  className={`seg-btn ${!busy && selected === h.id ? "on" : ""}`} disabled={busy} onClick={() => select(h.id)}
                  title={h.text}>{i + 1}</button>
              ))}
            </div>
          )}
          <button type="button" className="btn btn-primary" onClick={rep.show} disabled={busy}><Plus size={16} />Report a problem</button>
        </div>
      </header>

      <div className="ws-bar card">
        {view && <Stepper phase={phaseOf(view, busy)} />}
        <ProblemSummary view={view} state={state} />
      </div>

      <div className="ws-grid">
        <div className="span-7"><AgentsCard view={view} /></div>
        <div className="span-5"><LiveFeed items={feed} /></div>
        <section className="card span-12">
          <CardHead title="The ripple" sub="What this problem touches, wave by wave. Grey items keep their plan."
            right={
              <div className="legend">
                <span><i className="sw sw-origin" />The problem</span>
                <span><i className="sw sw-hit" />Affected</span>
                <span><i className="sw sw-idle" />Not affected</span>
                <span><i className="sw sw-ok" />Handled</span>
              </div>
            } />
          <RippleMap model={state.model} impact={view?.impact} resolved={view?.coordinator === "done" && !busy} animateKey={view?.id ?? "none"} />
        </section>
        <div className="span-7 col">{view && <PlanCard view={view} onNewProblem={rep.show} />}{view && <RecoveryPlan view={view} />}{view && <VenueCard view={view} />}</div>
        <div className="span-5 col">{view && <OutboxCard view={view} />}</div>
        <div className="span-12"><BehindTheScenes view={view} /></div>
      </div>
      {rep.dialog}
    </div>
  );
}

// ---- Outbox --------------------------------------------------------------------------

export function OutboxPage() {
  const { state } = useRipple();
  if (!state) return null;
  const withMsgs = state.history.filter((h) => (h.outbox ?? []).length);
  return (
    <div className="page">
      <header className="ws-head"><div className="ws-title"><h1>Outbox</h1><p className="sub">Every draft, per problem. Nothing is sent until you approve.</p></div></header>
      {withMsgs.length === 0 ? (
        <section className="card"><CardHead icon={<Inbox size={18} />} title="No drafts yet" /><p className="empty">Drafts appear here once the Coordinator has written them.</p></section>
      ) : (
        <div className="outbox-grid">{[...withMsgs].reverse().map((h) => <OutboxCard key={h.id} view={entryToView(h)} compact />)}</div>
      )}
    </div>
  );
}

// ---- Settings ------------------------------------------------------------------------

export function SettingsPage() {
  const { state, reset, busy } = useRipple();
  const [theme, choice, setChoice] = useTheme();
  const nav = useNavigate();
  if (!state) return null;
  return (
    <div className="page">
      <header className="ws-head"><div className="ws-title"><h1>Settings</h1><p className="sub">How this Ripple instance is set up. Values come from the backend.</p></div></header>
      <div className="settings-grid">
        <section className="card">
          <CardHead icon={<Server size={18} />} title="Agents" />
          <dl className="meta">
            <dt>Mode</dt><dd>{state.demo_mode ? "Replay: recorded agent runs, no Cominty credits used" : "Live: Cominty agents"}</dd>
            <dt>Change it</dt><dd>Set <code>DEMO_MODE</code> in <code>backend/.env</code>, then restart the server</dd>
            <dt>Venue search area</dt><dd>{state.search_area || "not set"}</dd>
            <dt>API</dt><dd>{API}</dd>
            <dt>Event id</dt><dd>{getEvent()}</dd>
          </dl>
        </section>
        <section className="card">
          <CardHead icon={<Sun size={18} />} title="Appearance" sub={`Now showing the ${theme} theme. Saved in this browser.`} />
          <div className="seg" role="radiogroup" aria-label="Theme">
            {(["light", "dark", "system"] as const).map((c) => (
              <button key={c} type="button" role="radio" aria-checked={choice === c} className={`seg-btn wide ${choice === c ? "on" : ""}`}
                onClick={() => setChoice(c)}>{c === "system" ? "Match my system" : c === "light" ? "Light" : "Dark"}</button>
            ))}
          </div>
        </section>
        <div className="span-all"><BehindTheScenes view={undefined} open /></div>
        <section className="card">
          <CardHead icon={<RotateCcw size={18} />} title="Start over" sub="Clears every problem, message and file of this event" />
          <button type="button" className="btn btn-danger" disabled={busy}
            onClick={async () => { if (confirm("Start the event over? This clears all problems, messages and files.")) { await reset(); nav("/app/setup"); } }}>
            <RotateCcw size={16} />Reset event
          </button>
        </section>
      </div>
    </div>
  );
}
