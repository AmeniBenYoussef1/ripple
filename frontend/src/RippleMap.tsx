// The ripple map: the disrupted node at the centre, wave i on ring i, untouched nodes grey outside.
import { useEffect, useMemo, useState } from "react";
import type { EventModel, Impact, ModelNode } from "./types";

const W = 1040;
const H = 640;
const CX = W / 2;
const CY = H / 2;
const NODE_W = 136;
const NODE_H = 56;
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

/** Ring i (1 = first wave): the first two keep their sizes, later waves go further out. */
function ringSize(i: number, _n?: number) {
  if (i <= 1) return RING_MIN;
  return { rx: Math.min(RING_MAX.rx + (i - 2) * 70, W / 2 - NODE_W / 2 - 6), ry: Math.min(RING_MAX.ry + (i - 2) * 30, H / 2 - NODE_H / 2 - 6) };
}

/** Two boxes overlap (with a small gap)? */
const clash = (a: { x: number; y: number }, b: { x: number; y: number }) =>
  Math.abs(a.x - b.x) < NODE_W + 8 && Math.abs(a.y - b.y) < NODE_H + 6;

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
  // Each affected item starts at the angle of the item it was reached from (short tree arrows),
  // then slides along its ring until it overlaps nothing already placed.
  const placed: Placed[] = [{ id: impact.origin, x: CX, y: CY, wave: 0 }];
  const angle = new Map<string, number>();
  const out = new Map<string, Set<string>>();
  for (const e of model.edges) (out.get(e.from) ?? out.set(e.from, new Set()).get(e.from)!).add(e.to);
  const inside = (x: number, y: number) => x - NODE_W / 2 >= 4 && x + NODE_W / 2 <= W - 4 && y - NODE_H / 2 >= 4 && y + NODE_H / 2 <= H - 4;
  impact.waves.forEach((ids, i) => {
    const { rx, ry } = ringSize(i + 1);
    const prev = i === 0 ? [impact.origin] : impact.waves[i - 1];
    const siblings = new Map<string, number>();
    ids.forEach((id, k) => {
      const parent = prev.find((p) => out.get(p)?.has(id)) ?? prev.find((p) => id.startsWith(p.replace(/s$/, "") + "_"));
      let a0 = i === 0 || parent === undefined || !angle.has(parent)
        ? (2 * Math.PI * k) / ids.length - Math.PI / 2
        : angle.get(parent)!;
      if (parent !== undefined && i > 0) {          // fan the children of one parent out a little
        const n = siblings.get(parent) ?? 0;
        siblings.set(parent, n + 1);
        a0 += (n % 2 ? -1 : 1) * Math.ceil(n / 2) * 0.16;
      }
      let spot = { x: CX + rx * Math.cos(a0), y: CY + ry * Math.sin(a0), a: a0 };
      search: for (const scale of [1, 1.12, 0.9, 1.24]) {
        for (let step = 0; step <= 90; step++) {
          const a = a0 + (step % 2 ? 1 : -1) * Math.ceil(step / 2) * 0.07;
          const p = { x: CX + rx * scale * Math.cos(a), y: CY + ry * scale * Math.sin(a) };
          if (inside(p.x, p.y) && !placed.some((q) => clash(p, q))) { spot = { ...p, a }; break search; }
        }
      }
      angle.set(id, spot.a);
      placed.push({ id, x: spot.x, y: spot.y, wave: i + 1 });
    });
  });
  // untouched items take free slots around the frame
  const taken = new Set(placed.map((p) => p.id));
  const rest = model.nodes.filter((nd) => !taken.has(nd.id)).sort((a, b) => a.id.localeCompare(b.id));
  const free = [...frameSlots(rest.length), ...frameSlots(99)].filter((sl, k, all) =>
    all.findIndex((o) => o.x === sl.x && o.y === sl.y) === k);
  rest.forEach((nd, k) => {
    const ideal = Math.floor((k * Math.min(free.length, 14)) / Math.max(rest.length, 1));
    const order = [...free.slice(ideal), ...free.slice(0, ideal)];
    const sl = order.find((o) => !placed.some((q) => clash(o, q))) ?? order[0];
    placed.push({ id: nd.id, x: sl.x, y: sl.y, wave: -1 });
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

/** A label on at most two lines of ~17 characters (cut at a space; the second line ends with … if needed). */
function twoLines(s: string, max = 17): string[] {
  if (s.length <= max) return [s];
  const words = s.split(/\s+/);
  let first = "";
  while (words.length && (first ? first.length + 1 + words[0].length : words[0].length) <= max) first = first ? `${first} ${words.shift()}` : words.shift()!;
  if (!first) first = words.shift()!.slice(0, max);
  const rest = words.join(" ");
  return rest ? [first, rest.length > max ? `${rest.slice(0, max - 1)}…` : rest] : [first];
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
  const checkedOk = new Set(impact?.checked_ok ?? []);

  const edges = model.edges.filter((e) => pos.has(e.from) && pos.has(e.to));
  // The ripple as a tree: one arrow into each affected item, from an item of the wave before
  // (or from its group, for the members of a group). Fewer crossings, and easy to follow.
  const tree = useMemo(() => {
    const parent = new Map<string, { from: string; member: boolean }>();
    if (!impact) return parent;
    const out = new Map<string, Set<string>>();
    for (const e of model.edges) (out.get(e.from) ?? out.set(e.from, new Set()).get(e.from)!).add(e.to);
    const levels = [[impact.origin], ...impact.waves];
    for (let k = 1; k < levels.length; k++) {
      for (const id of levels[k]) {
        const linked = (x: string) => out.get(x)?.has(id);
        const groupOf = (x: string) => id.startsWith(x.replace(/s$/, "") + "_");
        const p = levels[k - 1].find(linked) ?? levels[k - 1].find(groupOf) ?? levels.slice(0, k).flat().find(linked);
        if (p) parent.set(id, { from: p, member: !linked(p) });
      }
    }
    return parent;
  }, [model, impact]);
  const total = model.nodes.length;
  const hit = impact ? impact.waves.flat().length : 0;

  return (
    <div className="map-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="map" role="img"
        aria-label={impact ? `Ripple map: ${hit} of ${total} items affected in ${waves} waves` : "Event map, nothing affected"}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" style={{ fill: "var(--edge-live)" }} />
          </marker>
        </defs>

        {/* rings, outermost first */}
        {impact &&
          Array.from({ length: waves }, (_, k) => waves - k).map((i) => {
            const { rx, ry } = ringSize(i);
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

        {/* edges: the ripple's tree when something is disrupted, all links when calm */}
        {impact && [...tree.entries()].map(([to, { from, member }]) => {
          const a = pos.get(from);
          const b = pos.get(to);
          if (!a || !b) return null;
          const p = exitPoint(a.x, a.y, b.x, b.y);
          const q = exitPoint(b.x, b.y, a.x, a.y);
          return (
            <line key={to} x1={p.x} y1={p.y} x2={q.x} y2={q.y} markerEnd="url(#arrow)"
              className={`edge-live ${member ? "edge-member" : ""} fade ${b.wave <= shown ? "on" : ""}`} />
          );
        })}
        {!impact && edges.map((e, k) => {
          const a = pos.get(e.from)!;
          const b = pos.get(e.to)!;
          return <line key={k} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="edge-idle" />;
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
              {(() => {
                const lines = twoLines(node.label + (isGroup ? size : ""));
                const y0 = lines.length === 2 ? 17 : 23;
                return (
                  <>
                    {lines.map((l, i) => (
                      <text key={i} x={NODE_W / 2} y={y0 + i * 14} className={`node-label ${isOrigin ? "on-dark" : ""}`}>{l}</text>
                    ))}
                    <text x={NODE_W / 2} y={y0 + lines.length * 14 + 2} className={`node-sub ${isOrigin ? "on-dark" : ""} ${cls.includes("checked") ? "ok" : ""}`}>{sub}</text>
                  </>
                );
              })()}
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
