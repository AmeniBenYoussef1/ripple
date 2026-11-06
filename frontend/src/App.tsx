import { CheckCircle2, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Sidebar } from "./components/Shell";
import { HomePage, OutboxPage, SettingsPage, WorkspacePage } from "./pages";
import { SetupPage } from "./SetupPage";
import { WhatIfPage } from "./WhatIfPage";
import { RippleProvider, useRipple } from "./store";

function Layout() {
  const { state, error, clearError, notice, clearNotice } = useRipple();
  // after a success, bring the result into view (e.g. the Outbox after "Approve plan")
  useEffect(() => {
    if (!notice?.target) return;
    const t = window.setTimeout(() => document.getElementById(`${notice.target}-card`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 150);
    return () => window.clearTimeout(t);
  }, [notice]);
  const nav = useNavigate();
  const loc = useLocation();
  const checked = useRef(false);
  // A fresh event starts with its setup (once per visit).
  useEffect(() => {
    if (!state || checked.current) return;
    checked.current = true;
    if (!state.setup?.confirmed && state.history.length === 0 && loc.pathname === "/") nav("/setup");
  }, [state, loc.pathname, nav]);
  return (
    <div className="shell">
      <Sidebar />
      <main className="main">
        {error && (
          <div className="alert" role="alert">
            <span>{error}</span>
            <button type="button" className="icon-btn" aria-label="Dismiss" onClick={clearError}><X size={16} /></button>
          </div>
        )}
        {notice && (
          <div className="notice" role="status">
            <CheckCircle2 size={18} /><span>{notice.text}</span>
            <button type="button" className="icon-btn" aria-label="Dismiss" onClick={clearNotice}><X size={16} /></button>
          </div>
        )}
        {!state ? (
          <div className="loading">{error ? "" : "Loading Ripple…"}</div>
        ) : (
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/setup" element={<SetupPage />} />
            <Route path="/event" element={<WorkspacePage />} />
            <Route path="/whatif" element={<WhatIfPage />} />
            <Route path="/outbox" element={<OutboxPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        )}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <RippleProvider>
        <Layout />
      </RippleProvider>
    </BrowserRouter>
  );
}
