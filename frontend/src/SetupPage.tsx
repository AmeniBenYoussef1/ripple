// Event setup: drop the event files, let the Impact Analyst read them, check what Ripple understood.
import {
  CalendarClock, CheckCircle2, FileJson, FileSpreadsheet, FileText, Loader2, MapPin, PiggyBank,
  Sparkles, Trash2, UploadCloud, Users, Wand2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "./api";
import { CardHead, Pill } from "./components/ui";
import { RippleMap } from "./RippleMap";
import { useRipple } from "./store";
import type { EventModel, ModelNode } from "./types";
import { plainStep } from "./view";

const OK_TYPES = [".csv", ".pdf", ".txt", ".json"];
const MAX_FILES = 5;
const MAX_BYTES = 10 * 1024 * 1024;

interface Picked { file: File; problem: string | null }

const ext = (name: string) => name.slice(name.lastIndexOf(".")).toLowerCase();
const kb = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

function fileProblem(f: File): string | null {
  if (!OK_TYPES.includes(ext(f.name))) return "Not supported";
  if (f.size === 0) return "Empty";
  if (f.size > MAX_BYTES) return "Too large";
  return null;
}

function FileIcon({ name }: { name: string }) {
  const e = ext(name);
  if (e === ".csv") return <FileSpreadsheet size={18} />;
  if (e === ".json") return <FileJson size={18} />;
  return <FileText size={18} />;
}

function Source({ file }: { file?: string | null }) {
  return file ? <span className="source"><FileText size={12} />{file}</span> : null;
}

// ---- what Ripple understood (all from the event model) ----

function understood(m: EventModel) {
  const venue = m.nodes.find((n) => n.type === "resource" && typeof n.attrs.capacity === "number");
  const timeOf = (n: ModelNode) => String(n.attrs.start ?? n.attrs.time ?? "");
  const timeline = m.nodes
    .filter((n) => (n.type === "schedule_item" || n.type === "commitment") && timeOf(n))
    .sort((a, b) => timeOf(a).localeCompare(timeOf(b)));
  const groups = m.nodes.filter((n) => n.type === "group");
  const size = (n: ModelNode) => (typeof n.attrs.size === "number" ? n.attrs.size : 0);
  const word = (n: ModelNode) => n.label.toLowerCase().split(/\s+/)[0].replace(/s$/, "");
  // "Speaker 2" is shown inside "Speakers", not as its own line
  const parentOf = (n: ModelNode) => groups.find((o) => o !== n && o.source === n.source && size(o) > size(n) && word(o) === word(n));
  const top = groups.filter((g) => !parentOf(g)).sort((a, b) => size(b) - size(a));
  const budget = m.nodes.find((n) => n.type === "constraint" && typeof n.attrs.remaining_eur === "number");
  return { venue, timeline, top, children: (g: ModelNode) => groups.filter((x) => parentOf(x) === g), budget, timeOf };
}

function Understood({ model }: { model: EventModel }) {
  const u = useMemo(() => understood(model), [model]);
  const eur = (v: unknown) => (typeof v === "number" ? `€${v.toLocaleString("en-GB")}` : "—");
  return (
    <div className="understood">
      {u.venue && (
        <div className="u-block">
          <div className="u-head"><MapPin size={16} /><span>Venue</span><Source file={u.venue.source} /></div>
          <div className="u-big">{u.venue.label}</div>
          <div className="sub">{u.venue.attrs.capacity as number} people max
            {u.venue.attrs.address ? ` · ${u.venue.attrs.address}` : ""}
            {u.venue.attrs.date ? ` · ${new Date(String(u.venue.attrs.date)).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}` : ""}</div>
        </div>
      )}
      {u.budget && (
        <div className="u-block">
          <div className="u-head"><PiggyBank size={16} /><span>Budget left</span><Source file={u.budget.source} /></div>
          <div className="u-big">{eur(u.budget.attrs.remaining_eur)}</div>
          <div className="sub">of {eur(u.budget.attrs.total_eur)} · {eur(u.budget.attrs.committed_eur)} already committed</div>
        </div>
      )}
      <div className="u-block u-wide">
        <div className="u-head"><Users size={16} /><span>People</span></div>
        <ul className="u-groups">
          {u.top.map((g) => {
            const kids = u.children(g);
            return (
              <li key={g.id}>
                <span className="u-size">{typeof g.attrs.size === "number" && g.attrs.size > 1 ? g.attrs.size : ""}</span>
                <span className="u-label">{g.label}
                  {kids.length > 0 && <span className="sub"> · {kids.map((k) => k.label).join(", ")}</span>}</span>
                <Source file={g.source} />
              </li>
            );
          })}
        </ul>
      </div>
      <div className="u-block u-wide">
        <div className="u-head"><CalendarClock size={16} /><span>Day plan</span>
          <Source file={[...new Set(u.timeline.map((n) => n.source).filter(Boolean))].join(", ")} /></div>
        <ol className="u-timeline">
          {u.timeline.map((n) => (
            <li key={n.id}>
              <span className="u-time">{u.timeOf(n)}{n.attrs.end ? `–${n.attrs.end}` : ""}</span>
              <span>{n.label}{n.attrs.location && n.attrs.location !== "Main hall" ? <span className="sub"> · {String(n.attrs.location)}</span> : null}</span>
              {n.type === "commitment" && <span className="tag">to prepare</span>}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

// ---- page ----

export function SetupPage() {
  const { state, busy, analyze, confirmSetup } = useRipple();
  const nav = useNavigate();
  const [picked, setPicked] = useState<Picked[]>([]);
  const [drag, setDrag] = useState(false);
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<string[]>([]);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  // Show the Impact Analyst's steps while it reads the files.
  useEffect(() => {
    if (!running) return;
    setSteps([]);
    const es = new EventSource(api.streamUrl());
    const add = (text: string) => setSteps((s) => (s[0] === text ? s : [text, ...s].slice(0, 30)));
    es.addEventListener("setup_uploaded", (m) => add(`Sent ${JSON.parse((m as MessageEvent).data).file} to Cominty`));
    es.addEventListener("agent", (m) => {
      const d = JSON.parse((m as MessageEvent).data);
      if (d.status === "running" && (d.event === "ToolCall" || d.event === "UploadingFile") && d.description) {
        add(d.event === "UploadingFile" ? `Opening ${d.description}` : plainStep(d.description));
      }
    });
    return () => es.close();
  }, [running]);

  if (!state) return null;
  const setup = state.setup ?? { status: "not_analyzed" as const };
  const analyzed = setup.status === "analyzed";
  const replay = state.demo_mode;
  const good = picked.filter((p) => !p.problem);
  const tooMany = good.length > MAX_FILES;

  const add = (files: FileList | File[]) => {
    // copy now: the browser empties a FileList once the input is reset or the drop event ends
    const list = Array.from(files);
    setPicked((cur) => {
      const names = new Set(cur.map((p) => p.file.name));
      return [...cur, ...list.filter((f) => !names.has(f.name)).map((file) => ({ file, problem: fileProblem(file) }))];
    });
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    add(e.dataTransfer.files);
  };
  const useDemo = async () => {
    setLoadingDemo(true);
    try {
      const list = await api.demoFiles();
      const files = await Promise.all(list.map(async (f) => {
        const blob = await (await fetch(api.demoFileUrl(f.name))).blob();
        return new File([blob], f.name, { type: blob.type });
      }));
      setPicked(files.map((file) => ({ file, problem: fileProblem(file) })));
    } finally {
      setLoadingDemo(false);
    }
  };
  const run = async () => {
    setRunning(true);
    const ok = await analyze(good.map((p) => p.file));
    setRunning(false);
    if (ok) setPicked([]);
  };
  const looksRight = async () => {
    await confirmSetup();
    nav("/event");
  };

  return (
    <div className="page">
      <header className="ws-head">
        <div className="ws-title">
          <h1>Event setup</h1>
          <p className="sub">Give Ripple your event files. It reads them once and builds a map of your event, so it knows what a problem can touch.</p>
        </div>
        {analyzed && <Pill tone="ok">{setup.confirmed ? "Confirmed" : "Analyzed"}</Pill>}
      </header>

      <section className="card">
        <CardHead icon={<UploadCloud size={18} />} title="Your event files"
          sub="Participant list, schedule, contracts, budget… Up to 5 files: CSV, PDF, TXT or JSON, 10 MB each." />
        <div className={`dropzone ${drag ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)} onDrop={onDrop} onClick={() => input.current?.click()}
          role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
          aria-label="Add event files">
          <UploadCloud size={30} />
          <div><strong>Drop your files here</strong> or <span className="link-like">browse</span></div>
          <div className="sub">CSV · PDF · TXT · JSON</div>
          <input ref={input} type="file" multiple hidden accept={OK_TYPES.join(",")}
            onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
        </div>
        <div className="row wrap">
          <button type="button" className="btn btn-ghost" onClick={useDemo} disabled={loadingDemo || running}>
            {loadingDemo ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}Use the demo files (CodeSprint)
          </button>
          {picked.length > 0 && <button type="button" className="link-btn" onClick={() => setPicked([])} disabled={running}>Clear all</button>}
        </div>

        {picked.length > 0 && (
          <ul className="file-list">
            {picked.map((p) => (
              <li key={p.file.name} className={p.problem ? "bad" : ""}>
                <span className="file-ic"><FileIcon name={p.file.name} /></span>
                <span className="file-n">{p.file.name}</span>
                <span className="sub">{kb(p.file.size)}</span>
                <span className={`chip ${p.problem ? "chip-bad" : running ? "chip-working" : "chip-done"}`}>
                  {p.problem ?? (running ? (replay ? "Replaying" : "Sending") : "Ready")}
                </span>
                <button type="button" className="icon-btn" aria-label={`Remove ${p.file.name}`} disabled={running}
                  onClick={() => setPicked((cur) => cur.filter((x) => x !== p))}><Trash2 size={16} /></button>
              </li>
            ))}
          </ul>
        )}
        {tooMany && <p className="reason">Ripple can read at most {MAX_FILES} files at a time. Merge some (e.g. speakers and volunteers in one people list).</p>}

        <div className={`mode-note ${replay ? "replay" : "live"}`}>
          {replay
            ? <><strong>Replay mode.</strong> Ripple shows its recorded analysis of the five CodeSprint demo files (made with the real Impact Analyst on 6 Nov 2026). Your files are listed but not sent anywhere.</>
            : <><strong>Live.</strong> Your files are uploaded to Cominty and read by the Impact Analyst. Nothing is shared with anyone else.</>}
        </div>

        <div className="row wrap">
          <button type="button" className="btn btn-primary btn-lg" onClick={run}
            disabled={running || busy || good.length === 0 || tooMany}>
            {running ? <Loader2 size={18} className="spin" /> : <Wand2 size={18} />}{running ? "Reading your event…" : "Analyze my event"}
          </button>
          {analyzed && !running && picked.length === 0 && (
            <span className="sub">Analyzed {setup.files?.length ?? 0} files{setup.replay ? " (recorded analysis)" : ""}: {setup.files?.map((f) => f.name).join(", ")}</span>
          )}
        </div>

        {running && (
          <div className="setup-steps" aria-live="polite">
            <div className="row"><Loader2 size={16} className="spin" /><strong>Impact Analyst</strong><span className="sub">— understanding your event</span></div>
            <ol>{steps.map((s, i) => <li key={i} className={i === 0 ? "now" : ""}>{s}</li>)}</ol>
          </div>
        )}
      </section>

      {analyzed && !running && (
        <>
          <section className="card">
            <CardHead icon={<CheckCircle2 size={18} />} title="What Ripple understood"
              sub="Every fact comes from one of your files. If something is wrong, fix the file and analyze again."
              right={setup.replay ? <span className="badge badge-grey">Recorded analysis</span> : null} />
            <Understood model={state.model} />
          </section>

          <section className="card">
            <CardHead title="Your event as a map" sub="Everything Ripple will watch. When a problem hits, it lights up only what it touches." />
            <div className="setup-map"><RippleMap model={state.model} resolved={false} animateKey="setup" /></div>
          </section>

          <div className="setup-actions">
            <span className="sub">Next: report a problem and watch the ripple.</span>
            <button type="button" className="btn btn-primary btn-lg" onClick={looksRight}><CheckCircle2 size={18} />Looks right</button>
          </div>
        </>
      )}
    </div>
  );
}
