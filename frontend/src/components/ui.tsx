// Small shared UI pieces: card header, status pill, stepper, donut, duration.
import { Check } from "lucide-react";
import type { ReactNode } from "react";
import type { View } from "../types";

export function CardHead({ icon, title, right, sub }: { icon?: ReactNode; title: string; right?: ReactNode; sub?: string }) {
  return (
    <div className="card-head">
      <div className="card-title">
        {icon && <span className="card-icon">{icon}</span>}
        <div>
          <h2>{title}</h2>
          {sub && <p className="sub">{sub}</p>}
        </div>
      </div>
      {right}
    </div>
  );
}

export type Phase = "analysis" | "planning" | "review" | "done";
export const PHASES: { id: Phase; label: string }[] = [
  { id: "analysis", label: "Analysis" },
  { id: "planning", label: "Planning" },
  { id: "review", label: "Review" },
  { id: "done", label: "Done" },
];

/** Where a disruption stands, from real data only. */
export function phaseOf(v: View, busy: boolean): Phase {
  const drafts = v.outbox ?? [];
  if (!v.impact && v.status !== "questions") return "analysis";
  if (v.status === "questions") return "analysis";
  if (busy && v.coordinator !== "waiting_for_choice" && !drafts.length) return "planning";
  if (v.coordinator === "waiting_for_choice" || v.coordinator === "question") return "review";
  if (drafts.length && drafts.some((m) => m.status === "draft")) return "review";
  if (busy) return "planning";
  return "done";
}

export function statusText(v: View, busy: boolean): { text: string; tone: "alert" | "warn" | "ok" | "info" } {
  const p = phaseOf(v, busy);
  if (v.status === "questions") return { text: "Needs your answer", tone: "warn" };
  if (busy) return { text: p === "analysis" ? "Analysing" : "Agents working", tone: "alert" };
  if (v.coordinator === "waiting_for_choice") return { text: "Pick an option", tone: "warn" };
  if (v.coordinator === "question") return { text: "Needs your answer", tone: "warn" };
  if (p === "review") return { text: "Messages to review", tone: "warn" };
  return { text: "Handled", tone: "ok" };
}

export function Pill({ tone, children }: { tone: "alert" | "warn" | "ok" | "info"; children: ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export function Stepper({ phase }: { phase: Phase }) {
  const at = PHASES.findIndex((p) => p.id === phase);
  return (
    <ol className="stepper" aria-label="Progress">
      {PHASES.map((p, i) => {
        const state = i < at || phase === "done" ? "past" : i === at ? "now" : "next";
        return (
          <li key={p.id} className={`step ${state}`} aria-current={state === "now" ? "step" : undefined}>
            <span className="step-dot">{state === "past" ? <Check size={13} strokeWidth={3} /> : i + 1}</span>
            <span className="step-label">{p.label}</span>
            {i < PHASES.length - 1 && <span className="step-line" />}
          </li>
        );
      })}
    </ol>
  );
}

/** Approved vs drafts. */
export function Donut({ done, total, size = 64 }: { done: number; total: number; size?: number }) {
  const r = size / 2 - 6;
  const c = 2 * Math.PI * r;
  const part = total ? done / total : 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${done} of ${total} approved`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--cream)" strokeWidth="8" />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--teal)" strokeWidth="8"
        strokeDasharray={`${c * part} ${c}`} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dasharray 0.5s ease" }} />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" className="donut-text">{done}/{total}</text>
    </svg>
  );
}

export function duration(t?: { start: number; end?: number }, now = Date.now()): string | null {
  if (!t) return null;
  const ms = (t.end ?? now) - t.start;
  if (ms < 0) return null;
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}
