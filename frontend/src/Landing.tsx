// Ripple's public landing page. Every button works: "Try the demo" opens the app.
import {
  BellOff, CheckCircle2, ChevronDown, ExternalLink, FileUp, FlaskConical, Link2, Mail, MessageSquareText, PlayCircle,
  Radar, Scale, ShieldCheck, Waves,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Logo } from "./components/Shell";
import { HeroMap } from "./HeroMap";
import "./landing.css";
import { ThemeToggle } from "./theme";

/** Set VITE_VIDEO_URL (e.g. in frontend/.env.local) to turn on "Watch the video". */
const VIDEO_URL = (import.meta.env.VITE_VIDEO_URL as string | undefined)?.trim() ?? "";

const STEPS: { icon: ReactNode; title: string; text: string }[] = [
  { icon: <FileUp size={22} />, title: "Upload your event files", text: "Participant list, schedule, contracts, budget. Ripple reads them once and builds a map of your event." },
  { icon: <MessageSquareText size={22} />, title: "Report a problem in one sentence", text: "“The hall just cancelled.” Or paste the email you just received." },
  { icon: <Radar size={22} />, title: "Ripple traces the impact and finds options", text: "It follows the problem through your event, wakes only the agents it needs and ranks the options." },
  { icon: <CheckCircle2 size={22} />, title: "You approve, messages are ready", text: "One draft per group of people, a checklist for your team, a new schedule. You decide what goes out." },
];

const FEATURES: { icon: ReactNode; title: string; text: string }[] = [
  { icon: <Waves size={22} />, title: "The ripple map", text: "See, wave by wave, what a problem touches, and what it leaves alone." },
  { icon: <BellOff size={22} />, title: "Only affected agents run", text: "A late speaker doesn't start a venue search. Fewer agent runs, faster answers, lower cost." },
  { icon: <Scale size={22} />, title: "Fair, explained option ranking", text: "Hard limits first, then the group that loses most. Every rank comes with its reasons." },
  { icon: <FlaskConical size={22} />, title: "What-if previews", text: "Try another budget, more attendees or a delay. Computed in code: instant and free." },
  { icon: <Mail size={22} />, title: "One message per group", text: "Each group hears only what changed for them, sent to the people listed in your files." },
  { icon: <ShieldCheck size={22} />, title: "Nothing sent without you", text: "Every message is a draft until you approve it. You can edit it first." },
  { icon: <Link2 size={22} />, title: "A source for every venue", text: "Alternatives come with a link to their page, and their availability is always “to confirm”." },
];

const FAQ: { q: string; a: ReactNode }[] = [
  { q: "What is Ripple?", a: <>An emergency co-pilot for event organizers. When something goes wrong on the day (the venue cancels, a speaker is late, more people register), Ripple finds everything the problem touches, proposes ways out and prepares every message. When the next problem hits, only the parts it touches react.</> },
  { q: "Does it send messages automatically?", a: <>No. Ripple writes drafts. You review them, edit them if you want, and approve them. Approving marks them as ready; Ripple never sends anything by itself. You copy them or export the list of recipients and the revised schedule.</> },
  { q: "Where do the numbers and recipients come from?", a: <>From your own files, read by code. Group sizes, recipients and email addresses are taken from your participant list, your people list and your contracts, never typed by an AI agent. Each fact shows the file it comes from.</> },
  { q: "Are the venues really available?", a: <>The alternative venues are real places found online, each with a link to its page. Ripple never claims a venue is free: availability is always marked “to confirm”, and confirming it is on your checklist.</> },
  { q: "What powers the agents?", a: <>Four Cominty Managed Agents: an Impact Analyst, a Resource Finder, a Replanner and a Coordinator, sharing one skill that describes the expected answers. Deciding which agents run, ranking the options and the what-if previews are plain code.</> },
  { q: "What's the difference between Replay and Live?", a: <>In Live mode, Ripple calls the agents for real, which takes a little time and uses AI credits. Replay plays back real agent runs that were recorded earlier: free and instant, which is how the public demo works. Anything that was not recorded says “Live mode only”.</> },
  { q: "Is my data shared?", a: <>The demo uses fictional people, emails and addresses. Your event, its uploaded files and the recorded agent replies are stored as files on the Ripple server. In Live mode your files are sent to Cominty so the agents can read them. To place venues on the map, their public addresses are sent to OpenStreetMap, which also provides the map images. There are no accounts and no tracking.</> },
];

export function Landing() {
  return (
    <div className="landing">
      <header className="l-nav">
        <a href="#top" className="l-brand" aria-label="Ripple, back to top"><Logo size={28} /><span>Ripple</span></a>
        <nav className="l-links" aria-label="Sections">
          <a href="#features">Features</a>
          <a href="#how">How it works</a>
          <a href="#faq">FAQ</a>
        </nav>
        <div className="l-nav-end">
          <ThemeToggle />
          <Link to="/app" className="btn btn-primary">Try the demo</Link>
        </div>
      </header>

      <main id="top">
        <section className="l-hero">
          <div className="l-hero-text">
            <span className="l-kicker">Emergency co-pilot for event organizers</span>
            <h1>Drop one problem.<br /><span className="l-accent">Watch the ripple.</span></h1>
            <p className="l-lead">Ripple's AI agents find everything a disruption touches, propose fixes and prepare every message. Only what changed reacts.</p>
            <div className="l-cta">
              <Link to="/app" className="btn btn-primary btn-lg">Try the demo</Link>
              {VIDEO_URL ? (
                <a className="btn btn-outline btn-lg" href={VIDEO_URL} target="_blank" rel="noreferrer"><PlayCircle size={18} />Watch the video<ExternalLink size={14} /></a>
              ) : (
                <button type="button" className="btn btn-outline btn-lg" disabled title="The video link isn't set yet"><PlayCircle size={18} />Video coming soon</button>
              )}
            </div>
            <p className="l-note">No sign-in. The demo replays real agent runs on a fictional event.</p>
          </div>
          <HeroMap />
        </section>

        <section id="how" className="l-section">
          <h2>How it works</h2>
          <ol className="l-steps">
            {STEPS.map((s, i) => (
              <li key={s.title} className="l-card">
                <span className="l-step-n">{i + 1}</span>
                <span className="l-icon">{s.icon}</span>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="features" className="l-section">
          <h2>Features</h2>
          <ul className="l-features">
            {FEATURES.map((f) => (
              <li key={f.title} className="l-card">
                <span className="l-icon">{f.icon}</span>
                <h3>{f.title}</h3>
                <p>{f.text}</p>
              </li>
            ))}
          </ul>
        </section>

        <section id="faq" className="l-section l-faq">
          <h2>FAQ</h2>
          {FAQ.map((f) => (
            <details key={f.q} className="l-q">
              <summary><span>{f.q}</span><ChevronDown size={18} className="l-chev" /></summary>
              <p>{f.a}</p>
            </details>
          ))}
        </section>

        <section className="l-section l-final">
          <h2>See Ripple handle a crisis</h2>
          <p>Three problems, one fictional hackathon, real agent runs.</p>
          <Link to="/app" className="btn btn-primary btn-lg">Try the demo</Link>
        </section>
      </main>

      <footer className="l-footer">
        <span className="l-brand small"><Logo size={20} /><span>Ripple</span></span>
        <span>Built for the Cominty AI Engineer challenge · demo data is fictional</span>
      </footer>
    </div>
  );
}
