// Organizer-facing summary of a problem, and the technical "Behind the scenes" panel.
import { ChevronDown, Cpu, Mail, ShieldCheck, Users } from "lucide-react";
import { useState } from "react";
import { useRipple } from "../store";
import type { EventState, View } from "../types";
import { AGENTS, peopleAffected, unchanged } from "../view";

export function ProblemSummary({ view, state }: { view?: View; state: EventState }) {
  const [showAll, setShowAll] = useState(false);
  if (!view?.impact) {
    return <p className="sub">{view ? "Understanding what broke…" : "Report a problem to see who it affects."}</p>;
  }
  const people = peopleAffected(state.model, view);
  const msgs = view.outbox ?? [];
  const drafts = msgs.filter((m) => m.status === "draft").length;
  const same = unchanged(state.model, view);
  const shown = showAll ? same : same.slice(0, 4);
  return (
    <div className="summary">
      <div className="kpi">
        <span className="kpi-ic"><Users size={18} /></span>
        <div>
          <div className="kpi-label">People affected</div>
          <div className="kpi-value">{people?.count ?? 0}{people?.pending ? "+" : ""}</div>
          <div className="sub">{people?.groups.map((g) => `${g.label}${g.size > 1 ? ` (${g.size})` : ""}`).join(", ")}
            {people?.pending ? " + newcomers once messages are written" : ""}</div>
        </div>
      </div>
      <div className="kpi">
        <span className="kpi-ic"><Mail size={18} /></span>
        <div>
          <div className="kpi-label">Messages ready</div>
          <div className="kpi-value">{drafts}</div>
          <div className="sub">
            {msgs.length === 0
              ? view.coordinator === "waiting_for_choice" ? "written once you pick an option" : "none yet"
              : `${msgs.length - drafts} of ${msgs.length} approved`}
          </div>
        </div>
      </div>
      <div className="kpi kpi-wide">
        <span className="kpi-ic ok"><ShieldCheck size={18} /></span>
        <div>
          <div className="kpi-label">Unchanged</div>
          <div className="chips">
            {shown.map((l) => <span key={l} className="tag">{l}</span>)}
            {same.length > 4 && (
              <button type="button" className="link-btn" onClick={() => setShowAll(!showAll)}>
                {showAll ? "show less" : `+${same.length - 4} more`}
              </button>
            )}
            {same.length === 0 && <span className="sub">Everything is affected</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

const usd = (v: string | number | null | undefined) => (v == null || v === "" ? "—" : `$${Number(v).toFixed(3)}`);

/** Technical details for the curious (and for the jury): runs, skips, cost, savings, mode. */
export function BehindTheScenes({ view, open = false }: { view?: View; open?: boolean }) {
  const { state } = useRipple();
  if (!state) return null;
  const replay = state.demo_mode;
  const title = (a: string) => AGENTS.find((x) => x.name === a)?.title ?? a;
  const est = state.history.some((h) => h.saved_is_estimate);
  const thisCost = Object.values(view?.costs ?? {}).reduce((s, c) => s + Number(c), 0);
  const skipped = view?.routing ? Object.entries(view.routing.skipped) : [];
  const cached = view?.routing ? Object.keys(view.routing.cached) : [];
  return (
    <details className="card behind" open={open}>
      <summary>
        <span className="card-title"><span className="card-icon"><Cpu size={18} /></span>
          <span><h2>Behind the scenes</h2><span className="sub">Agent runs, costs and what Ripple skipped</span></span></span>
        <ChevronDown size={18} className="chev" />
      </summary>
      <div className="behind-grid">
        <dl className="meta">
          <dt>Mode</dt>
          <dd>
            <span className="mode" title="Set by DEMO_MODE in backend/.env when the server starts">
              <span className={`mode-opt ${replay ? "" : "on"}`}>Live</span>
              <span className={`mode-opt ${replay ? "on" : ""}`}>Replay</span>
            </span>
          </dd>
          <dt>Agent runs</dt><dd>{state.costs.length} in total{view?.routing ? ` · this problem: ${view.routing.run.map(title).join(", ") || "none"}` : ""}</dd>
          <dt>Cost</dt><dd>{usd(state.total_cost)} total{view?.costs ? ` · ${usd(thisCost)} this problem` : ""}
            {replay && <span className="tag tag-replay">recorded — $0 spent now</span>}</dd>
          <dt>≈ Saved</dt><dd>{usd(state.saved_total)}{est ? " (partly estimated from recorded runs)" : ""} by not waking agents a problem didn't need</dd>
        </dl>
        {view && <div>
          <div className="eyebrow">Skipped for this problem</div>
          {skipped.length === 0 && cached.length === 0 ? (
            <p className="sub">{view?.routing ? "None: this problem reached every agent." : "—"}</p>
          ) : (
            <ul className="skips">
              {skipped.map(([a, why]) => (
                <li key={a}><strong>{title(a)}</strong>: {why}
                  {view?.saved?.[a]?.cost ? <span className="sub"> · ≈ {usd(view.saved[a].cost)} saved{view.saved[a].estimate ? " (est.)" : ""}</span> : null}</li>
              ))}
              {cached.map((a) => <li key={a}><strong>{title(a)}</strong>: same input as before, answer reused</li>)}
            </ul>
          )}
        </div>}
      </div>
    </details>
  );
}
