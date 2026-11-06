import { X } from "lucide-react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/Shell";
import { HomePage, OutboxPage, SettingsPage, WorkspacePage } from "./pages";
import { RippleProvider, useRipple } from "./store";

function Layout() {
  const { state, error, clearError } = useRipple();
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
        {!state ? (
          <div className="loading">{error ? "" : "Loading Ripple…"}</div>
        ) : (
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/event" element={<WorkspacePage />} />
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
