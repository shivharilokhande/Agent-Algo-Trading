import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, getToken, setToken } from "./api";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import NewAnalysis from "./pages/NewAnalysis";
import RunLive from "./pages/RunLive";
import Runs from "./pages/Runs";
import Memory from "./pages/Memory";
import Settings from "./pages/Settings";
import Admin from "./pages/Admin";

const I = {
  home: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z"/></svg>,
  play: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/></svg>,
  clock: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>,
  brain: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M12 4a4 4 0 0 0-4 4v8a4 4 0 0 0 8 0V8a4 4 0 0 0-4-4z"/><path d="M8 10H5m14 0h-3M8 14H5m14 0h-3"/></svg>,
  gear: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.5-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.5 2 1.5a7 7 0 0 0 0 2.4l-2 1.5 2 3.5 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.5-2-1.5c.07-.4.1-.8.1-1.2z"/></svg>,
  shield: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>,
  chevron: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="m15 6-6 6 6 6"/></svg>,
};

function Shell({ children }: { children: React.ReactNode }) {
  const nav = useNavigate();
  const loc = useLocation();
  const [me, setMe] = useState<{ email: string; is_admin: boolean } | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    api.get<{ email: string; is_admin: boolean }>("/api/auth/me").then(setMe).catch(() => {});
  }, []);

  const links = [
    { to: "/", label: "Dashboard", icon: I.home, end: true },
    { to: "/new", label: "New Analysis", icon: I.play },
    { to: "/runs", label: "Run History", icon: I.clock },
    { to: "/memory", label: "Memory", icon: I.brain },
    { to: "/settings", label: "Settings", icon: I.gear },
  ];
  const onAdmin = loc.pathname.startsWith("/admin");

  return (
    <>
      <header className="topbar">
        <div className="brand"><span className="mark">A</span> AGENT<span className="tag">ALGO</span></div>
        <div className="center">
          <div className="mode-pills">
            <button className={`mode-pill ${!onAdmin ? "active" : ""}`} onClick={() => nav("/")}>Research</button>
            {me?.is_admin && (
              <button className={`mode-pill ${onAdmin ? "active" : ""}`} onClick={() => nav("/admin")}>Admin</button>
            )}
          </div>
        </div>
        <div className="right">
          <button className="mode-pill" title="Sign out" onClick={() => { setToken(null); nav("/login"); }}>Sign out</button>
          <div className="avatar" title={me?.email || ""}>{(me?.email || "?")[0].toUpperCase()}</div>
        </div>
      </header>
      <div className="layout">
        <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
          {links.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end as any}
              className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
              {l.icon}<span className="nav-label">{l.label}</span>
            </NavLink>
          ))}
          {me?.is_admin && (
            <>
              <div className="nav-sep" />
              <NavLink to="/admin" className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
                {I.shield}<span className="nav-label">Admin</span>
              </NavLink>
            </>
          )}
          <div className="spacer" />
          <button className="collapse-btn" onClick={() => setCollapsed(!collapsed)}>
            <span style={{ display: "inline-flex", width: 16, transform: collapsed ? "rotate(180deg)" : "none" }}>{I.chevron}</span>
            <span className="nav-label">Collapse</span>
          </button>
          <div className="powered">POWERED BY AGENTALGO</div>
          <div className="disclaimer">Research tool built on TradingAgents. Not financial, investment, or trading advice.</div>
        </aside>
        <main className="main">{children}</main>
      </div>
    </>
  );
}

function Protected({ children }: { children: React.ReactNode }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  return <Shell>{children}</Shell>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Protected><Dashboard /></Protected>} />
      <Route path="/new" element={<Protected><NewAnalysis /></Protected>} />
      <Route path="/runs" element={<Protected><Runs /></Protected>} />
      <Route path="/runs/:id" element={<Protected><RunLive /></Protected>} />
      <Route path="/memory" element={<Protected><Memory /></Protected>} />
      <Route path="/settings" element={<Protected><Settings /></Protected>} />
      <Route path="/admin" element={<Protected><Admin /></Protected>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
