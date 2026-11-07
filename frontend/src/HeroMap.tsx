// The landing page's animated mini ripple, laid out like the real one: Venue in the centre, what a
// problem touches on rings around it, untouched items outside. Plain React state + SVG + CSS.
// Layout rule: lines only run between a ring and the next, labels sit on the outward side of their
// dot, and each problem chip lands in an empty lane, so no line crosses a label or a chip.
import { useEffect, useState } from "react";

type Id = "venue" | "catering" | "schedule" | "speakers" | "attendees" | "volunteers" | "workshop" | "prizes" | "judge";
type Side = "below" | "left" | "right";

const C = { x: 260, y: 196 };
const NODES: { id: Id; label: string; x: number; y: number; side: Side }[] = [
  { id: "venue", label: "Venue", x: C.x, y: C.y, side: "below" },
  // inner ring
  { id: "catering", label: "Catering", x: 150, y: C.y, side: "left" },
  { id: "schedule", label: "Schedule", x: 370, y: C.y, side: "right" },
  // outer ring
  { id: "attendees", label: "200 attendees", x: 92, y: 300, side: "below" },
  { id: "volunteers", label: "Volunteers", x: 198, y: 358, side: "below" },
  { id: "workshop", label: "40 people", x: 322, y: 358, side: "below" },
  { id: "speakers", label: "Speakers", x: 428, y: 300, side: "below" },
  // outside the rings
  { id: "prizes", label: "Prizes", x: 62, y: 48, side: "below" },
  { id: "judge", label: "Online judge", x: 458, y: 48, side: "below" },
];
// the ripple's tree: one line into each item, from the ring before
const EDGES: [Id, Id][] = [
  ["venue", "catering"], ["venue", "schedule"], ["catering", "attendees"], ["catering", "volunteers"],
  ["schedule", "workshop"], ["schedule", "speakers"],
];

interface Scene { origin: Id; chip: string; chipAt: [number, number]; rings: number[]; waves: Id[][]; unchanged: Id[] }
const SCENES: Scene[] = [
  { origin: "venue", chip: "The hall just cancelled.", chipAt: [C.x, 146], rings: [112, 205],
    waves: [["catering"], ["schedule"], ["speakers", "attendees", "volunteers"]], unchanged: ["prizes", "judge"] },
  { origin: "schedule", chip: "A speaker is 2 hours late.", chipAt: [384, 146], rings: [70, 150],
    waves: [["speakers", "workshop"]], unchanged: [] },
];

// step: 0 calm · 1 chip falls · 2 origin hit · 3.. waves · then hold and reset
const TIMES = { calm: 900, fall: 1000, hit: 700, wave: 750, hold: 3000, reset: 1200 };

export function HeroMap() {
  const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const [scene, setScene] = useState(0);
  const [step, setStep] = useState(reduce ? 9 : 0);

  useEffect(() => {
    if (reduce) return;
    const s = SCENES[scene];
    const hold = 2 + s.waves.length + 1;
    const delay = step === 0 ? TIMES.calm : step === 1 ? TIMES.fall : step === 2 ? TIMES.hit
      : step < hold ? TIMES.wave : step === hold ? TIMES.hold : TIMES.reset;
    const t = window.setTimeout(() => {
      if (step > hold) {
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
  const on = (id: Id) => state(id) === "origin" || state(id) === "lit";

  return (
    <div className="hero-map" role="img"
      aria-label="An event map with the venue in the centre: a problem drops on it, rings spread, and only the items it touches light up, wave by wave. Prizes and the online judge stay unchanged.">
      <svg viewBox="0 0 520 420" aria-hidden="true">
        {/* rings around the problem */}
        {hit && s.rings.map((r, i) => (
          <circle key={`${scene}-${i}`} cx={o.x} cy={o.y} r={r} className="hm-ring" style={{ animationDelay: `${i * 0.75}s` }} />
        ))}
        {/* tree edges, drawn under the dots */}
        {EDGES.map(([a, b]) => (
          <line key={a + b} x1={pos[a].x} y1={pos[a].y} x2={pos[b].x} y2={pos[b].y}
            className={`hm-edge ${hit && on(a) && on(b) ? "on" : ""}`} />
        ))}
        {NODES.map((n) => {
          const st = state(n.id);
          const lx = n.side === "left" ? -18 : n.side === "right" ? 18 : 0;
          const ly = n.side === "below" ? 30 : 5;
          const anchor = n.side === "left" ? "end" : n.side === "right" ? "start" : "middle";
          return (
            <g key={n.id} className={`hm-node ${st}`} transform={`translate(${n.x} ${n.y})`}>
              {st === "origin" && <circle r="21" className="hm-halo" />}
              <circle r={st === "origin" ? 12 : 9} className="hm-dot" />
              <text x={lx} y={ly} textAnchor={anchor} className="hm-label">{n.label}</text>
              {st === "unchanged" && <text x={lx} y={ly + 15} textAnchor={anchor} className="hm-unchanged">unchanged</text>}
            </g>
          );
        })}
        {/* the problem drops into an empty lane above its target */}
        <g className={`hm-chip ${step >= 1 && step <= 2 + s.waves.length + 1 ? "in" : ""}`}
          style={{ transform: `translate(${s.chipAt[0]}px, ${step >= 1 ? s.chipAt[1] : s.chipAt[1] - 70}px)` }}>
          <rect x={-s.chip.length * 3.55 - 14} y={-15} width={s.chip.length * 7.1 + 28} height={30} rx={15} />
          <text y={5}>{s.chip}</text>
        </g>
      </svg>
    </div>
  );
}
