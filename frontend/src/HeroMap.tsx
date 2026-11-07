// The landing page's animated mini ripple: a problem drops on the event, rings spread, and only
// what it touches lights up, wave by wave. Plain React state + SVG + CSS transitions.
import { useEffect, useState } from "react";

type Id = "venue" | "catering" | "schedule" | "speakers" | "attendees" | "volunteers" | "workshop" | "prizes" | "judge";

// "up": the label sits above the dot (keeps the space under it free for the falling chip)
const NODES: { id: Id; label: string; x: number; y: number; up?: boolean }[] = [
  { id: "venue", label: "Venue", x: 250, y: 215 },
  { id: "catering", label: "Catering", x: 120, y: 132, up: true },
  { id: "schedule", label: "Schedule", x: 384, y: 132, up: true },
  { id: "attendees", label: "200 attendees", x: 100, y: 292 },
  { id: "volunteers", label: "Volunteers", x: 250, y: 342 },
  { id: "speakers", label: "Speakers", x: 452, y: 236 },
  { id: "workshop", label: "40 people", x: 392, y: 320 },
  { id: "prizes", label: "Prizes", x: 70, y: 40 },
  { id: "judge", label: "Online judge", x: 444, y: 40 },
];
const EDGES: [Id, Id][] = [
  ["venue", "catering"], ["venue", "schedule"], ["venue", "volunteers"], ["catering", "attendees"],
  ["schedule", "speakers"], ["schedule", "attendees"], ["schedule", "workshop"], ["catering", "volunteers"],
];

interface Scene { origin: Id; chip: string; chipAt: [number, number]; waves: Id[][]; unchanged: Id[] }
const SCENES: Scene[] = [
  { origin: "venue", chip: "The hall just cancelled.", chipAt: [250, 172], waves: [["catering"], ["schedule"], ["speakers", "attendees", "volunteers"]],
    unchanged: ["prizes", "judge"] },
  { origin: "schedule", chip: "A speaker is 2 hours late.", chipAt: [300, 82], waves: [["speakers", "workshop"]], unchanged: [] },
];

// step: 0 calm · 1 chip falls · 2 origin hit · 3.. waves · then hold and reset
const TIMES = [700, 1000, 700, 700, 700, 2800, 1100];

export function HeroMap() {
  const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const [scene, setScene] = useState(0);
  const [step, setStep] = useState(reduce ? 9 : 0);

  useEffect(() => {
    if (reduce) return;
    const s = SCENES[scene];
    const last = 2 + s.waves.length + 1;          // hold step
    const delay = step <= 2 ? TIMES[step] : step < last ? TIMES[3] : step === last ? TIMES[5] : TIMES[6];
    const t = window.setTimeout(() => {
      if (step > last) {                            // calm, then the next scene
        setScene((scene + 1) % SCENES.length);
        setStep(0);
      } else setStep(step + 1);
    }, delay);
    return () => window.clearTimeout(t);
  }, [scene, step, reduce]);

  const s = SCENES[scene];
  const hit = step >= 2 && step <= 2 + s.waves.length + 1;
  const lit = new Set<Id>(s.waves.slice(0, Math.max(0, step - 2)).flat());
  const showUnchanged = hit && step >= 2 + s.waves.length;
  const pos = Object.fromEntries(NODES.map((n) => [n.id, n])) as Record<Id, (typeof NODES)[number]>;
  const o = pos[s.origin];
  const state = (id: Id) => (hit && id === s.origin ? "origin" : hit && lit.has(id) ? "lit"
    : showUnchanged && s.unchanged.includes(id) ? "unchanged" : "calm");

  return (
    <div className="hero-map" role="img"
      aria-label="An event map: a problem drops on the venue, and only the items it touches light up, wave by wave. Prizes and the online judge stay unchanged.">
      <svg viewBox="0 0 520 410" aria-hidden="true">
        {/* rings */}
        {hit && [0, 1, 2].slice(0, s.waves.length === 1 ? 2 : 3).map((i) => (
          <circle key={`${scene}-${i}`} cx={o.x} cy={o.y} r={s.waves.length === 1 ? 70 : 150}
            className="hm-ring" style={{ animationDelay: `${i * 0.7}s` }} />
        ))}
        {/* edges */}
        {EDGES.map(([a, b]) => {
          const on = hit && (state(a) === "origin" || state(a) === "lit") && (state(b) === "lit" || state(b) === "origin");
          return <line key={a + b} x1={pos[a].x} y1={pos[a].y} x2={pos[b].x} y2={pos[b].y} className={`hm-edge ${on ? "on" : ""}`} />;
        })}
        {/* nodes */}
        {NODES.map((n) => {
          const st = state(n.id);
          return (
            <g key={n.id} className={`hm-node ${st}`} transform={`translate(${n.x} ${n.y})`}>
              {st === "origin" && <circle r="20" className="hm-halo" />}
              <circle r={st === "origin" ? 12 : 9} className="hm-dot" />
              <text y={n.up ? -17 : 27} className="hm-label">{n.label}</text>
              {st === "unchanged" && <text y={n.up ? -31 : 41} className="hm-unchanged">unchanged</text>}
            </g>
          );
        })}
        {/* the problem, dropping in */}
        <g className={`hm-chip ${step >= 1 && step <= 2 + s.waves.length + 1 ? "in" : ""}`}
          style={{ transform: `translate(${s.chipAt[0]}px, ${step >= 1 ? s.chipAt[1] : s.chipAt[1] - 70}px)` }}>
          <rect x={-s.chip.length * 3.55 - 14} y={-15} width={s.chip.length * 7.1 + 28} height={30} rx={15} />
          <text y={5}>{s.chip}</text>
        </g>
      </svg>
    </div>
  );
}
