// What-if simulator: change the budget, the number of attendees or delay one item, and see what
// would happen. Pure code on the server (score / propagate / route): no agent runs, nothing changes.
import { ArrowRight, FlaskConical, Loader2, RotateCcw, Users, Wallet, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "./api";
import type { WhatIf, WhatIfRipple } from "./api";
import { CardHead, Pill } from "./components/ui";
import { useRipple } from "./store";
import { AGENTS, problemTitle } from "./view";

const title = (a: string) => AGENTS.find((x) => x.name === a)?.title ?? a;
const VERDICT: Record<string, string> = { best: "v-best", ok: "v-ok", "over budget": "v-bad", "too small": "v-bad", "not possible": "v-bad" };

function Verdict({ v, rank }: { v: string; rank: number | null }) {
  return <span className={`verdict ${VERDICT[v] ?? "v-ok"}`}>{rank ? `#${rank} · ` : ""}{v === "best" ? "best option" : v}</span>;
}

function RippleSide({ r, empty }: { r?: WhatIfRipple | null; empty: string }) {
  if (!r) return <div className="wi-side calm"><p className="sub">{empty}</p></div>;
  return (
    <div className="wi-side">
      <div className="eyebrow">Affected</div>
      <div className="chips">{r.groups.length ? r.groups.map((g) => (
        <span key={g.id} className="tag">{g.label}{g.size && g.size > 1 ? ` · ${g.size}` : ""}</span>
      )) : <span className="sub">no group</span>}</div>
      {r.parts.length > 0 && <div className="chips">{r.parts.map((p) => <span key={p.id} className="tag tag-run">{p.label}: {p.effect}</span>)}</div>}
      <div className="eyebrow">Agents that would wake</div>
      <div className="chips">{r.run.length ? r.run.map((a) => <span key={a} className="tag tag-run">{title(a)}</span>)
        : <span className="sub">none</span>}</div>
      {Object.keys(r.skipped).length > 0 && (
        <ul className="skips">{Object.entries(r.skipped).map(([a, why]) => <li key={a}><strong>{title(a)}</strong> stays asleep: {why}</li>)}</ul>
      )}
    </div>
  );
}

export function WhatIfPage() {
  const { state, report, busy } = useRipple();
  const nav = useNavigate();
  const model = state?.model;
  const items = useMemo(() => (model?.nodes ?? [])
    .filter((n) => (n.type === "schedule_item" || n.type === "commitment") && (n.attrs.start || n.attrs.time))
    .sort((a, b) => String(a.attrs.start ?? a.attrs.time).localeCompare(String(b.attrs.start ?? b.attrs.time))), [model]);
  const [budget, setBudget] = useState("");
  const [people, setPeople] = useState("");
  const [node, setNode] = useState("");
  const [minutes, setMinutes] = useState(0);
  const [res, setRes] = useState<WhatIf | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);

  // start from today's numbers
  const resetInputs = (r: WhatIf) => {
    setBudget(r.budget.now != null ? String(r.budget.now) : "");
    setPeople(r.attendees.now != null ? String(r.attendees.now) : "");
    setNode("");
    setMinutes(0);
  };
  useEffect(() => {
    api.whatif({}).then((r) => { setRes(r); resetInputs(r); }).catch((e) => setErr(e.message));
  }, [state?.history.length]);

  // preview automatically, a moment after the organizer stops typing
  useEffect(() => {
    if (!res) return;
    const t = window.setTimeout(() => {
      setLoading(true);
      setErr(null);
      api.whatif({
        budget_eur: budget === "" ? undefined : Number(budget),
        attendees: people === "" ? undefined : Number(people),
        delay_node: node || undefined, delay_minutes: node ? minutes : 0,
      }).then(setRes).catch((e) => setErr(e.message)).finally(() => setLoading(false));
    }, 350);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [budget, people, node, minutes]);

  if (!state || !model) return null;
  const changed = res ? res.apply.length > 0 : false;

  const apply = async (a: WhatIf["apply"][number]) => {
    setApplying(a.text);
    try {
      if (a.kind === "budget") {
        await api.setBudget(a.value!);
        nav("/app/event");
      } else {
        nav("/app/event");
        await report(a.text);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(null);
    }
  };

  return (
    <div className="page">
      <header className="ws-head">
        <div className="ws-title">
          <h1>What if…?</h1>
          <p className="sub">Try a change before it happens. Ripple re-runs its own rules on a copy of your event.</p>
        </div>
        <Pill tone="info">Preview — no agents run, nothing changes</Pill>
      </header>

      <section className="card">
        <CardHead icon={<FlaskConical size={18} />} title="Change something"
          right={res ? <button type="button" className="btn btn-ghost btn-sm" onClick={() => resetInputs(res)}><RotateCcw size={14} />Back to today</button> : null} />
        <div className="wi-controls">
          <label className="relax"><span><Wallet size={14} /> Budget left (EUR)</span>
            <input className="text-input" type="number" min={0} step={500} value={budget} onChange={(e) => setBudget(e.target.value)} />
            <span className="sub">Today: {res?.budget.now != null ? `€${res.budget.now.toLocaleString("en-GB")}` : "—"}</span>
          </label>
          <label className="relax"><span><Users size={14} /> Attendees</span>
            <input className="text-input" type="number" min={1} step={10} value={people} onChange={(e) => setPeople(e.target.value)} />
            <span className="sub">Today: {res?.attendees.now ?? "—"}</span>
          </label>
          <div className="relax"><span>Delay</span>
            <select className="text-input" value={node} onChange={(e) => { setNode(e.target.value); if (!minutes) setMinutes(60); }} aria-label="What is late">
              <option value="">Nothing is late</option>
              {items.map((n) => <option key={n.id} value={n.id}>{String(n.attrs.start ?? n.attrs.time)} · {n.label}</option>)}
            </select>
            <select className="text-input" value={minutes} disabled={!node} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="How late">
              {[0, 15, 30, 60, 90, 120, 180].map((m) => <option key={m} value={m}>{m ? `${m} minutes late` : "on time"}</option>)}
            </select>
          </div>
        </div>
        {err && <p className="reason">{err}</p>}
      </section>

      {!res ? (
        <section className="card"><p className="empty"><Loader2 size={16} className="spin" /> Reading your event…</p></section>
      ) : (
        <>
          {res.options && (
            <section className={`card ${loading ? "dim" : ""}`}>
              <CardHead title="Your options" sub={`For "${problemTitle(res.options.text, res.options.detail).replace(/[.!?]$/, "")}". Ranked by code, side by side.`} />
              <table className="wi-table">
                <thead><tr><th>Option</th><th>Today</th><th aria-hidden="true" /><th>What-if</th></tr></thead>
                <tbody>
                  {res.options.rows.map((r) => (
                    <tr key={r.id} className={r.now.verdict !== r.whatif.verdict || r.now.rank !== r.whatif.rank ? "changed" : ""}>
                      <td><strong>{r.id}</strong> {r.title}</td>
                      <td><Verdict v={r.now.verdict} rank={r.now.rank} /></td>
                      <td><ArrowRight size={14} className="muted" /></td>
                      <td><Verdict v={r.whatif.verdict} rank={r.whatif.rank} />
                        {r.whatif.violations.length > 0 && <div className="sub">{r.whatif.violations.join("; ")}</div>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="fine">Costs that are still "to quote" can't be over budget yet: only known prices are checked.</p>
            </section>
          )}

          {(res.attendees.ripple || res.delay) && (
            <section className={`card ${loading ? "dim" : ""}`}>
              <CardHead title="Who would be affected" sub="The same rules Ripple uses for a real problem, on a copy." />
              {res.attendees.ripple && (
                <div className="wi-pair">
                  <div className="wi-what">{res.attendees.now} → {res.attendees.whatif} attendees</div>
                  <div className="wi-cols">
                    <RippleSide empty="Today: nothing to do, every agent sleeps." />
                    <RippleSide r={res.attendees.ripple} empty="" />
                  </div>
                </div>
              )}
              {res.delay && (
                <div className="wi-pair">
                  <div className="wi-what">{res.delay.label} {res.delay.minutes} minutes late</div>
                  <div className="wi-cols">
                    <RippleSide empty="Today: on time, every agent sleeps." />
                    <RippleSide r={res.delay.ripple} empty="" />
                  </div>
                </div>
              )}
            </section>
          )}

          <section className="card">
            <CardHead icon={<Zap size={18} />} title="Make it real" sub="Turn this preview into a real change. Only then do agents run." />
            {!changed ? <p className="empty">Change a number above to see what would happen.</p> : (
              <div className="stack-sm">
                {res.apply.map((a) => (
                  <div key={a.text} className="wi-apply">
                    <span>{a.kind === "budget" ? a.text : <>Report the problem <strong>"{a.text}"</strong></>}</span>
                    {a.replayable ? (
                      <button type="button" className="btn btn-primary btn-sm" disabled={busy || applying !== null} onClick={() => apply(a)}>
                        {applying === a.text ? <Loader2 size={14} className="spin" /> : null}
                        {a.kind === "budget" ? "Apply" : "Apply as a real problem"}</button>
                    ) : (
                      <span className="row"><span className="tag">Live mode only</span>
                        <button type="button" className="btn btn-primary btn-sm" disabled>Apply as a real problem</button></span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
