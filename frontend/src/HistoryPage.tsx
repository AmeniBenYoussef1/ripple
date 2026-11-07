// History: everything that happened to the event, from its setup to the last approval.
import { CheckCheck, ClipboardCheck, FileUp, GitBranch, History, Pencil, RotateCcw, SlidersHorizontal, TriangleAlert, Vote } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CardHead } from "./components/ui";
import { useRipple } from "./store";
import type { LogItem } from "./types";

const KINDS: { kind: LogItem["kind"]; label: string; icon: ReactNode }[] = [
  { kind: "setup", label: "Setup", icon: <FileUp size={16} /> },
  { kind: "problem", label: "Problems", icon: <TriangleAlert size={16} /> },
  { kind: "plan", label: "Plan versions", icon: <GitBranch size={16} /> },
  { kind: "decision", label: "Decisions", icon: <Vote size={16} /> },
  { kind: "constraint", label: "Limits changed", icon: <SlidersHorizontal size={16} /> },
  { kind: "edit", label: "Edits", icon: <Pencil size={16} /> },
  { kind: "approval", label: "Approvals", icon: <CheckCheck size={16} /> },
  { kind: "action", label: "Checklist", icon: <ClipboardCheck size={16} /> },
  { kind: "retry", label: "Agent retries", icon: <RotateCcw size={16} /> },
];

export function HistoryPage() {
  const { state, select } = useRipple();
  const nav = useNavigate();
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  if (!state) return null;
  const log = [...(state.log ?? [])].reverse();
  const shown = log.filter((l) => !hidden.has(l.kind));
  const count = (k: string) => log.filter((l) => l.kind === k).length;
  const toggle = (k: string) => setHidden((h) => { const n = new Set(h); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const day = (at: string) => new Date(at).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });

  return (
    <div className="page">
      <header className="ws-head">
        <div className="ws-title">
          <h1>History</h1>
          <p className="sub">Everything that happened to {state.model.title}: setup, problems, plan versions, your decisions, changed limits, edits and approvals.</p>
        </div>
        {(() => {
          const n = (state.plans ?? []).filter((p) => p.disruption).length;
          return n ? <span className="pill pill-info">{n} plan change{n === 1 ? "" : "s"}</span> : null;
        })()}
      </header>

      <section className="card">
        <CardHead icon={<History size={18} />} title="Timeline" sub="Newest first. Click a problem to open it." />
        <div className="chips filters">
          {KINDS.filter((k) => count(k.kind)).map((k) => (
            <button key={k.kind} type="button" className={`filter ${hidden.has(k.kind) ? "off" : ""}`} aria-pressed={!hidden.has(k.kind)}
              onClick={() => toggle(k.kind)}>{k.icon}{k.label} · {count(k.kind)}</button>
          ))}
        </div>
        {log.length === 0 ? (
          <p className="empty">Nothing has happened yet. Set up your event and report a problem: every step will be listed here.</p>
        ) : shown.length === 0 ? (
          <p className="empty">Every kind is filtered out. Turn one back on above.</p>
        ) : (
          <ol className="timeline">
            {shown.map((l, i) => {
              const k = KINDS.find((x) => x.kind === l.kind);
              const newDay = i === 0 || day(shown[i - 1].at) !== day(l.at);
              return (
                <li key={i} className={`tl-${l.kind}`}>
                  {newDay && <div className="tl-day">{day(l.at)}</div>}
                  <div className="tl-row">
                    <span className="tl-ic">{k?.icon}</span>
                    <time>{new Date(l.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</time>
                    <span className="tl-text">{l.text}</span>
                    {l.disruption && state.history.some((h) => h.id === l.disruption) && (
                      <button type="button" className="link-btn" onClick={() => { select(l.disruption!); nav("/app/event"); }}>Open</button>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </section>
    </div>
  );
}
