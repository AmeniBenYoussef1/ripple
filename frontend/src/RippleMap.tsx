// The ripple map: the disrupted node at the centre, wave i on ring i, untouched nodes grey outside.
import { useEffect, useMemo, useState } from "react";
import type { EventModel, Impact, ModelNode } from "./types";

const W = 1040;
const H = 640;
const CX = W / 2;
const CY = H / 2;
const NODE_W = 136;
const NODE_H = 46;
const RING_MIN = { rx: 215, ry: 128 };
const RING_MAX = { rx: 380, ry: 215 };

const EFFECT_TEXT: Record<string, string> = {
  moved: "to move",
  inform: "to inform",
  resize: "new quantity",
  insufficient: "too small",
  lost: "lost",
  check: "to check",
  rescore: "re-score",
  resized: "size changed",
};

interface Placed {
  id: string;
  x: number;
  y: number;
  wave: number; // 0 = origin, -1 = not affected
}

function ringSize(i: number, n: number) {
  if (n <= 1) return RING_MIN;
  const t = (i - 1) / (n - 1);
  return { rx: RING_MIN.rx + (RING_MAX.rx - RING_MIN.rx) * t, ry: RING_MIN.ry + (RING_MAX.ry - RING_MIN.ry) * t };
}

/** Fixed slots around the frame for nodes the disruption does not touch.
 *  Top and bottom rows first; the side columns only when more than 14 nodes need a place. */
function frameSlots(count: number): { x: number; y: number }[] {
  const rows: { x: number; y: number }[] = [];
  const sides: { x: number; y: number }[] = [];
  const cols = 7;
  const m = NODE_W / 2 + 8;
  for (let c = 0; c < cols; c++) rows.push({ x: m + (c * (W - 2 * m)) / (cols - 1), y: 32 });
  for (let c = cols - 1; c >= 0; c--) rows.push({ x: m + (c * (W - 2 * m)) / (cols - 1), y: H - 32 });
  for (let r = 1; r <= 4; r++) sides.push({ x: W - m, y: 32 + (r * (H - 64)) / 5 });
  for (let r = 4; r >= 1; r--) sides.push({ x: m, y: 32 + (r * (H - 64)) / 5 });
  return count <= rows.length ? rows : [...rows, ...sides];
}

const timeKey = (n: { attrs: Record<string, unknown> }) => String(n.attrs.start ?? n.attrs.time ?? "99");
const CALM_COLUMNS: { title: string; types: string[]; sort?: (a: ModelNode, b: ModelNode) => number }[] = [
  { title: "Places & budget", types: ["resource", "constraint"] },
  { title: "To prepare", types: ["commitment"], sort: (a, b) => timeKey(a).localeCompare(timeKey(b)) },
  { title: "Day plan", types: ["schedule_item"], sort: (a, b) => timeKey(a).localeCompare(timeKey(b)) },
  { title: "People", types: ["group"], sort: (a, b) => Number(b.attrs.size ?? 0) - Number(a.attrs.size ?? 0) },
];

function layout(model: EventModel, impact: Impact | undefined): Placed[] {
  if (!impact) {
    // Nothing wrong yet: a calm overview in columns, read left to right like the event itself.
    const placed: Placed[] = [];
    CALM_COLUMNS.forEach((col, c) => {
      const ids = model.nodes.filter((n) => col.types.includes(n.type)).sort(col.sort ?? (() => 0));
      const x = 110 + (c * (W - 220)) / (CALM_COLUMNS.length - 1);
      const step = Math.min(58, (H - 110) / Math.max(ids.length, 1));
      const top = 70 + ((H - 90) - step * ids.length) / 2;
      ids.forEach((n, k) => placed.push({ id: n.id, x, y: top + step * (k + 0.5), wave: -1 }));
    });
    return placed;
  }
  const placed: Placed[] = [{ id: impact.origin, x: CX, y: CY, wave: 0 }];
  const n = impact.waves.length;
  impact.waves.forEach((ids, i) => {
    const { rx, ry } = ringSize(i + 1, n);
    const m = ids.length;
    ids.forEach((id, k) => {
      const a = (2 * Math.PI * k) / m - Math.PI / 2 + (i % 2 ? Math.PI / m : 0);
      placed.push({ id, x: CX + rx * Math.cos(a), y: CY + ry * Math.sin(a), wave: i + 1 });
    });
  });
  const taken = new Set(placed.map((p) => p.id));
  const rest = model.nodes.filter((nd) => !taken.has(nd.id)).sort((a, b) => a.id.localeCompare(b.id));
  const slots = frameSlots(rest.length);
  rest.forEach((nd, k) => {
    const s = slots[Math.floor((k * slots.length) / Math.max(rest.length, 1)) % slots.length];
    placed.push({ id: nd.id, x: s.x, y: s.y, wave: -1 });
  });
  return placed;
}

/** Point where the line from a box centre towards (tx, ty) leaves the box. */
function exitPoint(x: number, y: number, tx: number, ty: number, pad = 4) {
  const dx = tx - x;
  const dy = ty - y;
  if (dx === 0 && dy === 0) return { x, y };
  const sx = (NODE_W / 2 + pad) / Math.abs(dx || 1e-9);
  const sy = (NODE_H / 2 + pad) / Math.abs(dy || 1e-9);
  const s = Math.min(sx, sy);
  return { x: x + dx * s, y: y + dy * s };
}

function truncate(s: string, max = 17) {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

interface Props {
  model: EventModel;
  impact?: Impact;
  resolved: boolean;
  animateKey: string;
}

export function RippleMap({ model, impact, resolved, animateKey }: Props) {
  const placed = useMemo(() => layout(model, impact), [model, impact]);
  const pos = useMemo(() => new Map(placed.map((p) => [p.id, p])), [placed]);
  const nodes = useMemo(() => new Map(model.nodes.map((n) => [n.id, n])), [model]);
  const waves = impact?.waves.length ?? 0;

  // Reveal the ripple wave by wave (respects "reduce motion").
  const [shown, setShown] = useState(waves);
  useEffect(() => {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (!impact || reduce) {
      setShown(waves);
      return;
    }
    setShown(0);
    let i = 0;
    const t = window.setInterval(() => {
      i += 1;
      setShown(i);
      if (i >= waves) window.clearInterval(t);
    }, 750);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animateKey, waves, !!impact]);   // replay only for a new disruption, not on every data refresh

  const visible = (p: Placed) => p.wave <= shown;
  const affected = (id: string) => (pos.get(id)?.wave ?? -1) >= 0;
  const checkedOk = new Set(impact?.checked_ok ?? []);

  const edges = model.edges.filter((e) => pos.has(e.from) && pos.has(e.to));
  const total = model.nodes.length;
  const hit = impact ? impact.waves.flat().length : 0;

  return (
    <div className="map-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="map" role="img"
        aria-label={impact ? `Ripple map: ${hit} of ${total} items affected in ${waves} waves` : "Event map, nothing affected"}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill="#B4502C" />
          </marker>
        </defs>

        {/* rings, outermost first */}
        {impact &&
          Array.from({ length: waves }, (_, k) => waves - k).map((i) => {
            const { rx, ry } = ringSize(i, waves);
            return (
              <g key={`ring-${i}`} className={`fade ${i <= shown ? "on" : ""}`}>
                <ellipse cx={CX} cy={CY} rx={rx + 70} ry={ry + 34} className="ring" style={{ fillOpacity: 0.07 + 0.06 * (waves - i) }} />
              </g>
            );
          })}

        {/* calm overview: column titles */}
        {!impact && CALM_COLUMNS.map((col, c) => (
          <text key={col.title} x={110 + (c * (W - 220)) / (CALM_COLUMNS.length - 1)} y={34} className="col-title">{col.title}</text>
        ))}

        {/* edges */}
        {edges.map((e, k) => {
          const a = pos.get(e.from)!;
          const b = pos.get(e.to)!;
          const live = impact && affected(e.from) && affected(e.to) && b.wave > a.wave;
          if (!live) {
            // once something is disrupted, only the ripple's own arrows are drawn
            return impact ? null : <line key={k} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="edge-idle" />;
          }
          const p = exitPoint(a.x, a.y, b.x, b.y);
          const q = exitPoint(b.x, b.y, a.x, a.y);
          return (
            <line key={k} x1={p.x} y1={p.y} x2={q.x} y2={q.y} markerEnd="url(#arrow)"
              className={`edge-live fade ${b.wave <= shown ? "on" : ""}`} />
          );
        })}

        {/* nodes */}
        {placed.map((p) => {
          const node = nodes.get(p.id);
          if (!node) return null;
          const isOrigin = p.wave === 0;
          const isGroup = node.type === "group";
          const effect = impact?.effects[p.id];
          const size = typeof node.attrs.size === "number" ? ` · ${node.attrs.size}` : "";
          let cls = "node idle";
          let sub = node.type.replace("_", " ");
          if (isOrigin) {
            cls = "node origin";
            sub = EFFECT_TEXT[effect ?? ""] ?? effect ?? "";
          } else if (p.wave > 0) {
            cls = resolved ? "node resolved" : "node hit";
            sub = resolved ? "handled" : EFFECT_TEXT[effect ?? ""] ?? effect ?? "affected";
          } else if (checkedOk.has(p.id)) {
            cls = "node checked";
            sub = "still fits ✓";
          }
          return (
            <g key={p.id} className={`fade ${visible(p) || p.wave < 0 ? "on" : ""}`} transform={`translate(${p.x - NODE_W / 2} ${p.y - NODE_H / 2})`}>
              <title>{`${node.label}${size}${node.source ? ` (from ${node.source})` : ""}`}</title>
              <rect width={NODE_W} height={NODE_H} rx={isGroup ? NODE_H / 2 : 10} className={cls} />
              <text x={NODE_W / 2} y={20} className={`node-label ${isOrigin ? "on-dark" : ""}`}>{truncate(node.label + (isGroup ? size : ""))}</text>
              <text x={NODE_W / 2} y={36} className={`node-sub ${isOrigin ? "on-dark" : ""} ${cls.includes("checked") ? "ok" : ""}`}>{sub}</text>
            </g>
          );
        })}
      </svg>
      <p className="map-caption">
        {impact
          ? `${hit} of ${total - 1} items affected (${impact.waves.map((w, i) => `wave ${i + 1}: ${w.length}`).join(", ")}, from the centre out). Items outside the rings keep their plan, and their agents stay asleep.`
          : "Nothing has gone wrong yet. Drop a problem in to see what it touches."}
      </p>
    </div>
  );
}
