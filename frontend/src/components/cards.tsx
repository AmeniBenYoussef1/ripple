// Workspace cards: agents, live updates, proposed plan, outbox.
import {
  Ban, ClipboardCheck, MessageSquareWarning, Square, SquareCheckBig,
  Activity, ArrowRightLeft, CalendarClock, CheckCircle2, ChevronDown, Clock, Coins, Copy, Download,
  ExternalLink, FileSpreadsheet, FileText, Inbox, Loader2, MinusCircle, Moon, Radar, RotateCcw, Search,
  Send, Sparkles, TriangleAlert, Users, X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import { useRipple } from "../store";
import type { FeedItem } from "../store";
import type { AgentName, AgentView, Candidate, Message, Scenario, Scored, View } from "../types";
import { AGENT_SUBTITLE, AGENTS, labelOf, plainStep, sizeOf } from "../view";
import { CardHead, Donut, duration } from "./ui";

const AGENT_ICON: Record<AgentName, ReactNode> = {
  impact_analyst: <Radar size={18} />,
  resource_finder: <Search size={18} />,
  replanner: <CalendarClock size={18} />,
  coordinator: <Send size={18} />,
};

function StatusChip({ a }: { a: AgentView }) {
  const map: Record<AgentView["status"], [ReactNode, string]> = {
    working: [<Loader2 size={14} className="spin" />, "Working"],
    done: [<CheckCircle2 size={14} />, "Done"],
    skipped: [<MinusCircle size={14} />, "Skipped"],
    cached: [<RotateCcw size={14} />, "Cached"],
    waiting: [<Clock size={14} />, "Waiting"],
    idle: [<Moon size={14} />, "Asleep"],
  };
  const [icon, text] = map[a.status];
  return <span className={`chip chip-${a.status}`}>{icon}{text}</span>;
}

// ---- agents ----------------------------------------------------------------------------

export function AgentsCard({ view }: { view?: View }) {
  const { timing, busy } = useRipple();
  const [, tick] = useState(0);
  useEffect(() => {
    if (!busy) return;
    const t = window.setInterval(() => tick((n) => n + 1), 500);
    return () => window.clearInterval(t);
  }, [busy]);
  return (
    <section className="card">
      <CardHead icon={<Users size={18} />} title="Agents are working together"
        sub="Each agent wakes only if the problem touches its part of the event"
        right={busy ? <span className="live-tag"><span className="live-dot" />Live</span> : null} />
      <ul className="agent-list">
        {AGENTS.map(({ name, title }) => {
          const a = view?.agents[name] ?? { status: "idle" as const, detail: "Asleep until something goes wrong" };
          const d = a.status === "skipped" || a.status === "idle" ? null : duration(timing[name]);
          return (
            <li key={name} className={`agent-row ${a.status}`}>
              <span className="agent-ic">{AGENT_ICON[name]}</span>
              <div className="agent-main">
                <div className="agent-name">{title} <span className="agent-sub">— {AGENT_SUBTITLE[name].toLowerCase()}</span></div>
                <div className="agent-desc" aria-live={a.status === "working" ? "polite" : undefined}>{plainStep(a.detail) || "—"}</div>
              </div>
              <div className="agent-side">
                <StatusChip a={a} />
                <span className="agent-time" title="Measured in this browser">{d ?? ""}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ---- live updates --------------------------------------------------------------------

const FEED_ICON: Record<FeedItem["kind"], ReactNode> = {
  step: <Activity size={15} />,
  finding: <Sparkles size={15} />,
  cost: <Coins size={15} />,
  draft: <FileText size={15} />,
  skip: <Moon size={15} />,
  error: <TriangleAlert size={15} />,
};

export function LiveFeed({ items }: { items: FeedItem[] }) {
  return (
    <section className="card feed-card">
      <CardHead icon={<Activity size={18} />} title="Live updates" sub="What Ripple found, newest first" />
      {items.length === 0 ? (
        <p className="empty">Updates appear here as soon as you report a problem.</p>
      ) : (
        <ol className="feed">
          {items.map((it, i) => (
            <li key={i} className={`feed-item ${it.kind}`}>
              <span className="feed-ic">{FEED_ICON[it.kind]}</span>
              <span className="feed-text">{it.text}</span>
              <time className="feed-time">
                {it.at ? new Date(it.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "summary"}
              </time>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

// ---- proposed plan -----------------------------------------------------------------

const core = (s: string) => s.split("(")[0].trim().toLowerCase();

function venueOf(s: Scenario | undefined, candidates: Candidate[]): { name: string; cand?: Candidate } | null {
  const name = s?.changes?.find((c) => c.replace_with)?.replace_with;
  if (!name) return null;
  const cand = candidates.find((c) => core(c.name).includes(core(name)) || core(name).includes(core(c.name)));
  return { name, cand };
}

function costText(v: number | null | undefined) {
  return v === null || v === undefined ? "to quote" : `€${v.toLocaleString("en-GB")} est.`;
}

function ScoreLines({ s }: { s: Scored }) {
  const { state } = useRipple();
  const m = state!.model;
  return (
    <ul className="score">
      <li><span>Worst impact</span><strong>{s.worst_group ? labelOf(m, s.worst_group) : "nobody"} · {s.worst_burden_min} min/person <em>est.</em></strong></li>
      <li><span>Total impact</span><strong>{s.total_burden_person_min.toLocaleString("en-GB")} person-min <em>est.</em></strong></li>
      <li><span>Extra cost</span><strong>{costText(s.extra_cost_eur)}</strong></li>
      <li><span>Spare seats</span><strong>{s.spare_seats ?? "unknown"}</strong></li>
      <li><span>Still to confirm</span><strong>{s.unconfirmed}</strong></li>
      {s.tied && <li className="tie"><span>Tie</span><strong>Same score as {s.tied_with.join(", ")}: your call</strong></li>}
    </ul>
  );
}

/** The Coordinator asked a question before writing the messages: answer it here. */
function CoordinatorQuestion({ view }: { view: View }) {
  const { state, answer, busy, pending } = useRipple();
  const [text, setText] = useState("");
  const replay = !!state?.demo_mode;
  const recorded = state?.replayable?.[view.id]?.answers ?? [];
  const sending = pending === "answer";
  return (
    <div className="question-box" id="question-card">
      <div className="row"><Sparkles size={16} /><strong>The Coordinator needs one answer before writing the messages</strong></div>
      <p className="sub">It asks instead of guessing, so no message says something wrong.</p>
      {(view.coordinator_question ?? []).map((q, i) => (
        <div key={i} className="stack-sm">
          <p>{q.prompt}</p>
          <div className="row wrap">
            {q.options.map((o) => (
              <button key={o} type="button" className="btn btn-secondary btn-sm" disabled={busy || replay}
                title={replay ? "Live mode only" : undefined} onClick={() => answer(o)}>{o}</button>
            ))}
          </div>
        </div>
      ))}
      {replay && recorded.length > 0 && (
        <div className="stack-sm">
          <span className="eyebrow">Recorded answer</span>
          {recorded.map((r) => (
            <button key={r} type="button" className="btn btn-primary" disabled={busy} onClick={() => answer(r)}>
              {sending ? <Loader2 size={16} className="spin" /> : <Send size={16} />}{r}
            </button>
          ))}
        </div>
      )}
      <div className="answer-row">
        <input className="text-input" value={text} onChange={(e) => setText(e.target.value)} disabled={busy || replay}
          placeholder={replay ? "Your own answer: Live mode only" : "Or write your own answer…"}
          onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) answer(text.trim()); }} aria-label="Your answer" />
        <button type="button" className="btn btn-secondary" disabled={busy || replay || !text.trim()} onClick={() => answer(text.trim())}>
          {sending && !replay ? <Loader2 size={16} className="spin" /> : <Send size={16} />}Send
        </button>
      </div>
      {replay && <p className="fine">Replay shows the recorded answer. Other answers need Live mode.</p>}
    </div>
  );
}

export function PlanCard({ view, onNewProblem }: { view: View; onNewProblem: () => void }) {
  const { choose, busy, report, state, pending, decide } = useRipple();
  const [note, setNote] = useState("");
  const liveOnly = (sid: string) => !!state?.demo_mode && !(state.replayable?.[view.id]?.options ?? []).includes(sid);
  const [asking, setAsking] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const othersRef = useRef<HTMLDivElement>(null);
  const scored = view.scored ?? [];

  if (view.status === "questions" && view.questions?.length) {
    return (
      <section className="card">
        <CardHead icon={<Sparkles size={18} />} title="A quick question first" sub="Ripple asks instead of guessing" />
        {view.questions.map((q, i) => (
          <div key={i} className="stack-sm">
            <p>{q.prompt}</p>
            <div className="row wrap">{q.options.map((o) => (
              <button key={o} type="button" className="btn btn-secondary" disabled={busy} onClick={() => report(o)}>{o}</button>
            ))}</div>
          </div>
        ))}
      </section>
    );
  }

  if (!scored.length) {
    const why = view.routing?.skipped.replanner;
    return (
      <section className="card">
        <CardHead icon={<CalendarClock size={18} />} title="Proposed plan" />
        <p className="empty">
          {view.routing
            ? why ? `No new plan needed: ${why}. The Coordinator informs the people concerned directly.` : "The Replanner is preparing options…"
            : "Report a problem to get a plan."}
        </p>
      </section>
    );
  }

  const rejected = new Set(view.rejected ?? []);
  const open = scored.filter((s) => !rejected.has(s.id));
  if (!open.some((s) => s.feasible) && !view.chosen) return <NoFitCard view={view} />;
  const top = scored.find((s) => s.id === view.chosen) ?? open.find((s) => s.feasible) ?? scored[0];
  const others = scored.filter((s) => s.id !== top.id);
  const scen = view.scenarios?.find((s) => s.id === top.id);
  const venue = venueOf(scen, view.candidates ?? []);
  const toConfirm = scen?.to_confirm ?? [];
  const canChoose = view.coordinator === "waiting_for_choice" && !busy;
  const chosen = view.chosen === top.id;
  const approving = pending === `choose:${top.id}`;

  return (
    <section className="card">
      <CardHead icon={<CalendarClock size={18} />} title="Proposed plan"
        sub="Agents propose options; Ripple ranks them: hard limits first, then the group that loses most"
        right={chosen ? <span className="badge badge-ok"><CheckCircle2 size={13} />Plan approved</span>
          : top.tied ? <span className="badge badge-coral">#{top.rank} tied</span>
          : top.feasible ? <span className="badge badge-ok">Best score</span> : <span className="badge badge-grey">No feasible plan</span>} />

      <div className="plan-top">
        <div className="eyebrow">Option {top.id}</div>
        <h3 className="plan-title">{top.title}</h3>
        {venue?.cand ? (
          <div className="venue">
            <span>{venue.name}{venue.cand.capacity ? ` · ${venue.cand.capacity} people` : ""}</span>
            <a className="btn btn-secondary btn-sm" href={venue.cand.url} target="_blank" rel="noreferrer">View venue<ExternalLink size={13} /></a>
            <span className="badge badge-coral small">availability to confirm</span>
          </div>
        ) : venue ? <div className="venue"><span className="sub">Change:</span><span>{venue.name}</span></div> : null}
        {top.feasible ? <ScoreLines s={top} /> : <p className="reason">Fails a hard limit: {top.violations.join("; ")}</p>}
      </div>

      {toConfirm.length > 0 && (
        <div className="confirm-box">
          <div className="eyebrow">To confirm before anything is booked</div>
          <ul>
            {(showAll ? toConfirm : toConfirm.slice(0, 4)).map((t, i) => <li key={i}>{t}</li>)}
          </ul>
          {toConfirm.length > 4 && (
            <button type="button" className="link-btn" onClick={() => setShowAll(!showAll)}>
              {showAll ? "Show less" : `Show all ${toConfirm.length}`}<ChevronDown size={14} className={showAll ? "flip" : ""} />
            </button>
          )}
        </div>
      )}

      {view.coordinator === "question" && <CoordinatorQuestion view={view} />}

      {(!chosen || approving) && (view.coordinator === "waiting_for_choice" || approving) && (
        <div className="plan-actions">
          <button type="button" className="btn btn-primary" disabled={!canChoose || !top.feasible || liveOnly(top.id)}
            onClick={() => choose(top.id)} aria-busy={approving}>
            {approving ? <Loader2 size={16} className="spin" /> : <CheckCircle2 size={16} />}
            {approving ? "Writing the messages…" : "Approve plan"}
          </button>
          {liveOnly(top.id) && <span className="tag" title="This option's messages were not recorded">Live mode only</span>}
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setAsking(!asking)} aria-expanded={asking}>
            <ArrowRightLeft size={16} />Request changes
          </button>
          <button type="button" className="btn btn-ghost" disabled={busy}
            onClick={() => { if (confirm(`Reject option ${top.id}? Ripple will show the next best option.`)) void decide("rejected", top.id); }}>
            <Ban size={16} />Reject
          </button>
        </div>
      )}
      {asking && (
        <div className="request">
          <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} className="note-input"
            placeholder={`What should change in option ${top.id}? (saved with the time)`} aria-label="What should change" />
          <button type="button" className="btn btn-secondary" disabled={!note.trim()}
            onClick={() => { void decide("changes_requested", top.id, note); setNote(""); setAsking(false); }}>Save my request</button>
          <button type="button" className="btn btn-ghost" onClick={() => { setAsking(false); othersRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }); othersRef.current?.focus(); }}>
            Choose another option
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => { setAsking(false); onNewProblem(); }}>Report a new problem</button>
        </div>
      )}

      <Decisions view={view} />

      {others.length > 0 && (
        <div className="others" ref={othersRef} tabIndex={-1}>
          <div className="eyebrow">Other options</div>
          {others.map((s) => (
            <div key={s.id} className={`other ${s.feasible && !rejected.has(s.id) ? "" : "infeasible"}`}>
              <div className="other-main">
                <div className="other-title"><span className="other-id">{s.id}</span>{s.title}</div>
                <div className="sub">
                  {s.feasible
                    ? `${s.tied ? `#${s.rank} tied` : `#${s.rank}`} · worst ${s.worst_burden_min} min/person est. · cost ${costText(s.extra_cost_eur)}${s.spare_seats != null ? ` · ${s.spare_seats} spare seats` : ""}`
                    : <span className="reason">Fails a hard limit: {s.violations.join("; ")}</span>}
                </div>
              </div>
              {rejected.has(s.id) && <span className="badge badge-grey">Rejected</span>}
              {s.feasible && !rejected.has(s.id) && view.coordinator === "waiting_for_choice" && (liveOnly(s.id)
                ? <span className="row"><span className="tag" title="This option's messages were not recorded">Live mode only</span>
                    <button type="button" className="btn btn-secondary btn-sm" disabled>Choose {s.id}</button></span>
                : <button type="button" className="btn btn-secondary btn-sm" disabled={!canChoose} onClick={() => choose(s.id)}>
                    {pending === `choose:${s.id}` ? <Loader2 size={14} className="spin" /> : null}Choose {s.id}</button>)}
              {view.chosen === s.id && <span className="badge badge-ok">Chosen</span>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ---- decisions ----

const DECISION_WORDS = { approved: "Approved", changes_requested: "Asked for changes to", rejected: "Rejected" };

export function Decisions({ view }: { view: View }) {
  const list = view.decisions ?? [];
  if (!list.length) return null;
  return (
    <div className="decisions">
      <div className="eyebrow">Your decisions</div>
      <ul>{list.map((d, i) => (
        <li key={i}><time>{new Date(d.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</time>
          <span><strong>{DECISION_WORDS[d.kind]} option {d.option}</strong>{d.note ? ` · "${d.note}"` : ""}</span></li>
      ))}</ul>
    </div>
  );
}

// ---- no option fits ----

export function NoFitCard({ view }: { view: View }) {
  const { state, relax, replan, busy, pending } = useRipple();
  const budgetNode = state?.model.nodes.find((n) => n.type === "constraint" && typeof n.attrs.remaining_eur === "number");
  const [budget, setBudget] = useState(String(budgetNode?.attrs.remaining_eur ?? ""));
  const [slack, setSlack] = useState(String(view.relaxed?.capacity_slack ?? 0));
  const [date, setDate] = useState("");
  const replay = !!state?.demo_mode;
  const scored = view.scored ?? [];
  const rejected = new Set(view.rejected ?? []);
  return (
    <section className="card nofit">
      <CardHead icon={<MessageSquareWarning size={18} />} title="No option fits all your limits"
        sub="Ripple won't pick an option that breaks a hard limit. Tell it what you can relax." />
      <ul className="nofit-reasons">
        {scored.map((s) => (
          <li key={s.id}><strong>Option {s.id}</strong> {s.title}
            <div className="reason">{rejected.has(s.id) ? "You rejected it." : s.violations.join("; ") || "—"}</div></li>
        ))}
      </ul>
      <div className="eyebrow">What can you relax?</div>
      <div className="relax-grid">
        <label className="relax"><span>Budget left (EUR)</span>
          <input className="text-input" type="number" min={0} step={100} value={budget} onChange={(e) => setBudget(e.target.value)} />
          <button type="button" className="btn btn-secondary btn-sm" disabled={busy || budget === ""}
            onClick={() => relax({ budget_eur: Number(budget) })}>Use this budget</button>
        </label>
        <label className="relax"><span>People who can follow from an overflow space</span>
          <input className="text-input" type="number" min={0} step={5} value={slack} onChange={(e) => setSlack(e.target.value)} />
          <button type="button" className="btn btn-secondary btn-sm" disabled={busy || slack === ""}
            onClick={() => relax({ capacity_slack: Number(slack) })}>Accept this</button>
        </label>
        <label className="relax"><span>Another date or time</span>
          <input className="text-input" value={date} onChange={(e) => setDate(e.target.value)} disabled={replay}
            placeholder={replay ? "Live mode only" : "e.g. any Saturday in November"} />
          <button type="button" className="btn btn-secondary btn-sm" disabled={busy || replay || !date.trim()}
            onClick={() => replan(date)}>{pending === "replan" ? <Loader2 size={14} className="spin" /> : null}Ask for new options</button>
        </label>
      </div>
      <p className="fine">Budget and capacity re-check the same options in code, instantly. A new date needs new options from the Replanner{replay ? ", so it works in Live mode only" : ""}.</p>
    </section>
  );
}

// ---- recovery plan (the Coordinator's checklist) ----

const PRIORITY = { high: "badge-coral", medium: "badge-grey", low: "badge-soft" };

export function RecoveryPlan({ view }: { view: View }) {
  const { toggleAction } = useRipple();
  const actions = view.actions ?? [];
  const done = actions.filter((a) => a.status === "done").length;
  return (
    <section className="card" id="recovery-card">
      <CardHead icon={<ClipboardCheck size={18} />} title="Recovery plan"
        sub="What your team does next. Tick items as they are done; Ripple saves it."
        right={actions.length ? <span className="sub"><strong>{done}</strong> of {actions.length} done</span> : null} />
      {actions.length === 0 ? (
        <p className="empty">{view.coordinator === "waiting_for_choice" ? "The checklist appears once you approve an option."
          : view.coordinator === "question" ? "The checklist appears once you answer the Coordinator's question."
          : "No actions for this problem."}</p>
      ) : (
        <>
          <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={actions.length} aria-valuenow={done}>
            <span style={{ width: `${(100 * done) / actions.length}%` }} />
          </div>
          <ul className="actions">
            {actions.map((a) => (
              <li key={a.id} className={a.status}>
                <button type="button" className="check" role="checkbox" aria-checked={a.status === "done"}
                  aria-label={`${a.title}: ${a.status === "done" ? "done" : "to do"}`} onClick={() => toggleAction(a.id, a.status !== "done")}>
                  {a.status === "done" ? <SquareCheckBig size={20} /> : <Square size={20} />}
                </button>
                <div className="action-main">
                  <div className="action-title">{a.title}</div>
                  {a.description && <div className="sub">{a.description}</div>}
                  <div className="action-meta">
                    {a.owner && <span className="tag"><Users size={12} />{a.owner}</span>}
                    {a.deadline && <span className="tag"><Clock size={12} />{a.deadline}</span>}
                    <span className={`badge ${PRIORITY[a.priority]} small`}>{a.priority}</span>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

// ---- outbox --------------------------------------------------------------------------

export function OutboxCard({ view, compact = false }: { view: View; compact?: boolean }) {
  const { state, approve, busy } = useRipple();
  const [review, setReview] = useState<string | null>(null);
  const msgs = view.outbox ?? [];
  const approved = msgs.filter((m) => m.status === "approved").length;
  const drafts = msgs.filter((m) => m.status === "draft");
  const m = state!.model;
  const open = msgs.find((x) => x.id === review);
  return (
    <section className="card" id={compact ? undefined : "outbox-card"}>
      <CardHead icon={<Inbox size={18} />} title="Outbox"
        sub={compact ? view.text : view.chosen ? `Drafts for option ${view.chosen}` : "One draft per affected group"}
        right={msgs.length ? <Donut done={approved} total={msgs.length} size={56} /> : null} />
      {msgs.length === 0 ? (
        <p className="empty">{view.coordinator === "waiting_for_choice"
          ? "Approve an option and the Coordinator writes a message for every affected group."
          : "No messages yet."}</p>
      ) : (
        <ul className="msg-list">
          {msgs.map((msg) => {
            const size = sizeOf(m, msg.group);
            return (
              <li key={msg.id} className="msg-row">
                <span className={`msg-dot ${msg.status}`} aria-hidden="true" />
                <div className="msg-main">
                  <div className="msg-group">{labelOf(m, msg.group)}{size && size > 1 ? ` · ${size}` : ""}</div>
                  <div className="sub ellipsis">{msg.subject}</div>
                </div>
                <span className={`chip ${msg.status === "approved" ? "chip-done" : "chip-waiting"}`}>{msg.status === "approved" ? "Approved" : "Draft"}</span>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setReview(msg.id)}>Review</button>
              </li>
            );
          })}
        </ul>
      )}
      {drafts.length > 0 && (
        <button type="button" className="btn btn-primary btn-block" disabled={busy} onClick={() => approve(drafts.map((d) => d.id))}>
          <CheckCircle2 size={16} />Approve all {drafts.length}
        </button>
      )}
      {(view.files ?? []).length > 0 && (
        <div className="files">
          {(view.files ?? []).map((f) => (
            <a key={f.name} className="file" href={api.fileUrl(f.name)} download={f.name}>
              {f.kind === "ics" ? <CalendarClock size={18} /> : <FileSpreadsheet size={18} />}
              <span className="file-main">
                <span className="file-name">{f.name}</span>
                <span className="sub">{f.kind === "ics" ? `Revised schedule · ${f.items} items` : `Who gets which update · ${f.rows} people`}</span>
              </span>
              <Download size={16} />
            </a>
          ))}
        </div>
      )}
      {msgs.length > 0 && <p className="fine">Nothing is sent until you approve, and Ripple never sends anything by itself.</p>}
      {open && <ReviewDialog msg={open} group={labelOf(m, open.group)} onClose={() => setReview(null)}
        onApprove={() => { approve([open.id]); setReview(null); }} />}
    </section>
  );
}

function ReviewDialog({ msg, group, onClose, onApprove }: { msg: Message; group: string; onClose: () => void; onApprove: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => ref.current?.showModal(), []);
  return (
    <dialog ref={ref} className="dialog" onClose={onClose} aria-labelledby="review-title">
      <div className="dialog-head">
        <h2 id="review-title">{msg.subject}</h2>
        <button type="button" className="icon-btn" aria-label="Close" onClick={() => ref.current?.close()}><X size={18} /></button>
      </div>
      <dl className="meta">
        <dt>For</dt><dd>{group}</dd>
        <dt>To</dt><dd>{msg.to}</dd>
        <dt>Channel</dt><dd>{msg.channel}</dd>
        <dt>Status</dt><dd>{msg.status}</dd>
      </dl>
      <pre className="body">{msg.body}</pre>
      <div className="row-end">
        <button type="button" className="btn btn-ghost" onClick={() => navigator.clipboard?.writeText(`${msg.subject}\n\n${msg.body}`)}><Copy size={15} />Copy</button>
        <button type="button" className="btn btn-secondary" onClick={() => ref.current?.close()}>Close</button>
        {msg.status === "draft" && <button type="button" className="btn btn-primary" onClick={onApprove}><CheckCircle2 size={16} />Approve</button>}
      </div>
    </dialog>
  );
}
