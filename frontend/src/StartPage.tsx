// The public Start screen: your event files (or a sample event) + what went wrong -> Ripple works.
// Your own files create a NEW event for you only; a sample event is your own copy of a recorded one.
import {
  ArrowLeft, CheckCircle2, Download, FileJson, FileSpreadsheet, FileText, Info, Loader2, Sparkles, Trash2, UploadCloud, Wand2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, PENDING, setEvent } from "./api";
import type { VisitorStatus } from "./api";
import { Logo } from "./components/Shell";
import { Understood } from "./SetupPage";
import { ThemeToggle } from "./theme";
import type { EventState } from "./types";
import { plainStep } from "./view";
import "./landing.css";

const OK = [".csv", ".pdf", ".txt", ".json"];
const MAX_FILES = 5;
const SAMPLE_PROBLEMS = ["The hall just cancelled.", "Speaker 2's train is 2 hours late.", "30 late registrations."];
const ext = (n: string) => n.slice(n.lastIndexOf(".")).toLowerCase();
const size = (n: number) => (n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

type Phase = "form" | "creating" | "analyzing" | "understood";

export function StartPage() {
  const nav = useNavigate();
  const [status, setStatus] = useState<VisitorStatus | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [sample, setSample] = useState(false);
  const [text, setText] = useState("");
  const [drag, setDrag] = useState(false);
  const [phase, setPhase] = useState<Phase>("form");
  const [error, setError] = useState<{ text: string; offerSample: boolean } | null>(null);
  const [steps, setSteps] = useState<string[]>([]);
  const [made, setMade] = useState<EventState | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { api.visitorStatus().then(setStatus).catch(() => setStatus(null)); }, []);

  const maxMb = status?.max_file_mb ?? 5;
  const problem = (f: File) => (!OK.includes(ext(f.name)) ? "Not supported" : f.size === 0 ? "Empty"
    : f.size > maxMb * 1024 * 1024 ? `Larger than ${maxMb} MB` : null);
  const good = files.filter((f) => !problem(f));
  const tooMany = good.length > MAX_FILES;
  const liveOff = status !== null && (!status.live || !status.budget_left || status.analyses_left === 0);
  const sampleText = SAMPLE_PROBLEMS.includes(text.trim());
  const canStart = phase === "form" && (sample ? !text.trim() || sampleText : good.length > 0 && !tooMany && !liveOff);

  const add = (list: FileList | File[]) => {
    const arr = Array.from(list);                  // copy now: the browser empties the list afterwards
    setSample(false);
    setFiles((cur) => [...cur, ...arr.filter((f) => !cur.some((c) => c.name === f.name))]);
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); };
  const fail = (e: unknown) => {
    const err = e instanceof ApiError ? e : null;
    setError({ text: err?.message ?? "Something went wrong. Please try again.",
      offerSample: !!err && ["limit", "live-off", "public", "not-event"].includes(err.kind) });
    setPhase("form");
  };

  const openSample = async (problemText: string) => {
    setError(null);
    setPhase("creating");
    try {
      const s = await api.visitorSample();
      setEvent(s.event_id);
      if (problemText.trim()) sessionStorage.setItem(PENDING, problemText.trim());
      nav("/app/event");
    } catch (e) {
      fail(e);
    }
  };

  const start = async () => {
    setError(null);
    if (sample) return openSample(sampleText ? text : "");
    setPhase("creating");
    try {
      const ev = await api.visitorEvent("My event");
      setEvent(ev.event_id);
      setPhase("analyzing");
      // show the Impact Analyst's steps while it reads the files
      setSteps([]);
      const es = new EventSource(api.streamUrl(), { withCredentials: true });
      es.addEventListener("setup_uploaded", (m) => setSteps((s) => [`Sent ${JSON.parse((m as MessageEvent).data).file}`, ...s].slice(0, 8)));
      es.addEventListener("agent", (m) => {
        const d = JSON.parse((m as MessageEvent).data);
        if (d.status === "running" && d.event === "ToolCall" && d.description) setSteps((s) => [plainStep(d.description), ...s].slice(0, 8));
      });
      try {
        const r = await api.analyze(good);
        if (r.status === "questions") {
          setError({ text: `Ripple needs more information about these files: ${(r.questions ?? []).map((q) => q.prompt).join(" ")}`, offerSample: true });
          setPhase("form");
          return;
        }
        setMade(r.state);
        setPhase("understood");
      } finally {
        es.close();
      }
    } catch (e) {
      fail(e);
    }
  };

  const proceed = async () => {
    if (!made) return;
    await api.confirmSetup().catch(() => undefined);
    if (text.trim()) sessionStorage.setItem(PENDING, text.trim());
    nav("/app/event");
  };

  return (
    <div className="landing start">
      <header className="l-nav">
        <Link to="/" className="l-brand" aria-label="Ripple home"><Logo size={28} /><span>Ripple</span></Link>
        <div className="l-nav-end">
          <ThemeToggle />
          <Link to="/" className="btn btn-ghost"><ArrowLeft size={16} />Back</Link>
        </div>
      </header>

      <main className="start-main">
        <h1 className="start-title">Start Ripple</h1>
        <p className="l-lead">Give Ripple your event files and tell it what went wrong. Or start from a sample event.</p>

        {phase === "understood" && made ? (
          <section className="card">
            <div className="card-head"><div className="card-title"><span className="card-icon"><CheckCircle2 size={18} /></span>
              <div><h2>What Ripple understood</h2><p className="sub">Every fact comes from one of your files. Check it, then Ripple handles your problem.</p></div></div></div>
            <Understood model={made.model} />
            <div className="row-end">
              <button type="button" className="btn btn-ghost" onClick={() => { setPhase("form"); setMade(null); }}>Something is wrong</button>
              <button type="button" className="btn btn-primary btn-lg" onClick={proceed}>
                <CheckCircle2 size={18} />{text.trim() ? "Looks right, handle my problem" : "Looks right, open my event"}
              </button>
            </div>
          </section>
        ) : (
          <>
            <section className="card">
              <div className="card-head"><div className="card-title"><span className="card-icon"><UploadCloud size={18} /></span>
                <div><h2>Your event files</h2><p className="sub">Up to {MAX_FILES} files: CSV, PDF, TXT or JSON, {maxMb} MB each.</p></div></div></div>
              <div className={`dropzone ${drag ? "over" : ""} ${sample ? "dim" : ""}`} role="button" tabIndex={0} aria-label="Add event files"
                onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
                onClick={() => input.current?.click()} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}>
                <UploadCloud size={30} />
                <div><strong>Drop your files here</strong> or <span className="link-like">browse</span></div>
                <input ref={input} type="file" multiple hidden accept={OK.join(",")}
                  onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
              </div>
              {files.length > 0 && (
                <ul className="file-list">
                  {files.map((f) => {
                    const p = problem(f);
                    const Icon = ext(f.name) === ".csv" ? FileSpreadsheet : ext(f.name) === ".json" ? FileJson : FileText;
                    return (
                      <li key={f.name} className={p ? "bad" : ""}>
                        <span className="file-ic"><Icon size={18} /></span>
                        <span className="file-n">{f.name}</span>
                        <span className="sub">{size(f.size)}</span>
                        <span className={`chip ${p ? "chip-bad" : "chip-done"}`}>{p ?? "Ready"}</span>
                        <button type="button" className="icon-btn" aria-label={`Remove ${f.name}`} disabled={phase !== "form"}
                          onClick={() => setFiles((c) => c.filter((x) => x !== f))}><Trash2 size={16} /></button>
                      </li>
                    );
                  })}
                </ul>
              )}
              {tooMany && <p className="reason">At most {MAX_FILES} files: merge some, e.g. speakers and volunteers in one people list.</p>}
              <details className="best">
                <summary>Files Ripple understands best</summary>
                <ul>
                  <li><strong>Participant list</strong> (CSV): name, email, and columns like team, dietary or workshop.</li>
                  <li><strong>People</strong> (CSV): speakers, volunteers, staff, with a role column and emails.</li>
                  <li><strong>Schedule</strong> (JSON, CSV or TXT): sessions with start and end times, the venue and its capacity.</li>
                  <li><strong>Contracts</strong> (PDF or TXT): suppliers with their contact email, times and quantities.</li>
                  <li><strong>Budget</strong> (JSON or CSV): total, spent and what is left.</li>
                </ul>
              </details>
              <div className="row wrap">
                <a className="btn btn-ghost" href={api.samplePackUrl()} download><Download size={16} />Download a sample pack</a>
                <button type="button" className={`btn ${sample ? "btn-primary" : "btn-secondary"}`} onClick={() => { setSample(!sample); setFiles([]); setError(null); }}
                  aria-pressed={sample}><Sparkles size={16} />{sample ? "Sample event selected" : "Use a sample event"}</button>
                <span className="sub">{sample ? "CodeSprint 2026, a fictional hackathon. Replays real agent runs: instant and free." : ""}</span>
              </div>
            </section>

            <section className="card">
              <div className="card-head"><div className="card-title"><span className="card-icon"><Wand2 size={18} /></span>
                <div><h2>What went wrong?</h2><p className="sub">One sentence, or paste the email you received.</p></div></div></div>
              <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} disabled={phase !== "form"}
                placeholder={sample ? "Pick one of the problems below" : "e.g. The venue just cancelled because of a water leak."} aria-label="What went wrong" />
              {sample && (
                <div className="quick">
                  <span className="eyebrow">Recorded problems for the sample event</span>
                  {SAMPLE_PROBLEMS.map((p, i) => (
                    <button key={p} type="button" className={`quick-btn ${text === p ? "on" : ""}`} onClick={() => setText(p)}>
                      <span className="quick-n">{i + 1}</span>{p}</button>
                  ))}
                  {text.trim() && !sampleText && <p className="sub">The sample event replays recorded problems only. Pick one above, or use your own files for anything else.</p>}
                </div>
              )}
              <p className="note"><Info size={15} />Use fictional data — don't upload personal information. Your files and event are deleted after {status?.ttl_hours ?? 24} hours.</p>
            </section>

            {error && (
              <div className="alert start-alert" role="alert">
                <span>{error.text}</span>
                {error.offerSample && (
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => openSample(SAMPLE_PROBLEMS[0])}>
                    <Sparkles size={14} />Use a sample event</button>
                )}
              </div>
            )}

            <div className="start-go">
              <span className="sub tries">
                {status === null ? "" : !status.live ? "Live agents are off on this server: sample events only."
                  : !status.budget_left ? "Today's free live tries are used up: sample events only."
                  : `Live tries left today: ${status.analyses_left} file analysis, ${status.problems_left} problems.`}
              </span>
              <button type="button" className="btn btn-primary btn-lg" disabled={!canStart} onClick={start}>
                {phase === "form" ? <Wand2 size={18} /> : <Loader2 size={18} className="spin" />}
                {phase === "creating" ? "Preparing your event…" : phase === "analyzing" ? "Reading your files…" : "Start Ripple"}
              </button>
            </div>

            {phase === "analyzing" && (
              <div className="setup-steps" aria-live="polite">
                <div className="row"><Loader2 size={16} className="spin" /><strong>Impact Analyst</strong><span className="sub">— understanding your event (about a minute)</span></div>
                <ol>{steps.map((s, i) => <li key={i} className={i === 0 ? "now" : ""}>{s}</li>)}</ol>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
