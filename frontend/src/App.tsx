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
import Watchlists from "./pages/Watchlists";
import Automations from "./pages/Automations";
import Compare from "./pages/Compare";
import Portfolio from "./pages/Portfolio";
import FnoDesk from "./pages/FnoDesk";
import Ensemble from "./pages/Ensemble";
import Studio from "./pages/Studio";
import Research from "./pages/Research";

const I = {
  home: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z"/></svg>,
  play: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/></svg>,
  clock: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>,
  brain: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M12 4a4 4 0 0 0-4 4v8a4 4 0 0 0 8 0V8a4 4 0 0 0-4-4z"/><path d="M8 10H5m14 0h-3M8 14H5m14 0h-3"/></svg>,
  gear: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.5-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.5 2 1.5a7 7 0 0 0 0 2.4l-2 1.5 2 3.5 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.5-2-1.5c.07-.4.1-.8.1-1.2z"/></svg>,
  shield: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>,
  chevron: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="m15 6-6 6 6 6"/></svg>,
  list: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/></svg>,
  bolt: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M13 2 4 14h6l-1 8 9-12h-6z"/></svg>,
  bell: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" width="16" height="16" stroke="currentColor"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 8-3 8h18s-3-1-3-8"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>,
  wallet: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M16 15h2"/></svg>,
  layers: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="m12 2 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></svg>,
  wrench: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M14.7 6.3a4.5 4.5 0 0 0-6 6L3 18l3 3 5.7-5.7a4.5 4.5 0 0 0 6-6L14 13l-3-3z"/></svg>,
  book: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/></svg>,
  candle: <svg viewBox="0 0 24 24" fill="none" strokeWidth="2"><path d="M7 4v3m0 10v3M7 7h-2v10h4V7zM17 2v4m0 12v4M17 6h-2v12h4V6z" transform="translate(1,0)"/></svg>,
};

function Shell({ children }: { children: React.ReactNode }) {
  const nav = useNavigate();
  const loc = useLocation();
  const [me, setMe] = useState<{ email: string; is_admin: boolean } | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    api.get<{ email: string; is_admin: boolean }>("/api/auth/me").then(setMe).catch(() => {});
    const poll = () =>
      api.get<any[]>("/api/alerts?unread_only=true").then((a) => setUnread(a.length)).catch(() => {});
    poll();
    const t = setInterval(poll, 30000);
    window.addEventListener("agentalgo-alerts-read", poll);
    return () => { clearInterval(t); window.removeEventListener("agentalgo-alerts-read", poll); };
  }, []);

  const links = [
    { to: "/", label: "Dashboard", icon: I.home, end: true },
    { to: "/new", label: "New Analysis", icon: I.play },
    { to: "/fno", label: "F&O Desk", icon: I.candle },
    { to: "/watchlists", label: "Watchlists", icon: I.list },
    { to: "/runs", label: "Run History", icon: I.clock },
    { to: "/automations", label: "Automations", icon: I.bolt },
    { to: "/portfolio", label: "Portfolio", icon: I.wallet },
    { to: "/ensemble", label: "Ensemble", icon: I.layers },
    { to: "/research", label: "Research", icon: I.book },
    { to: "/studio", label: "Agent Studio", icon: I.wrench },
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
          <button className="mode-pill" title="Alerts" style={{ position: "relative" }}
            onClick={() => nav("/automations")}>
            {I.bell}
            {unread > 0 && (
              <span style={{
                position: "absolute", top: 0, right: 0, background: "var(--accent)", color: "#fff",
                borderRadius: 999, fontSize: 10, fontWeight: 800, padding: "1px 5px", lineHeight: "12px",
              }}>{unread}</span>
            )}
          </button>
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
      <Route path="/fno" element={<Protected><FnoDesk /></Protected>} />
      <Route path="/runs" element={<Protected><Runs /></Protected>} />
      <Route path="/watchlists" element={<Protected><Watchlists /></Protected>} />
      <Route path="/automations" element={<Protected><Automations /></Protected>} />
      <Route path="/compare" element={<Protected><Compare /></Protected>} />
      <Route path="/portfolio" element={<Protected><Portfolio /></Protected>} />
      <Route path="/ensemble" element={<Protected><Ensemble /></Protected>} />
      <Route path="/research" element={<Protected><Research /></Protected>} />
      <Route path="/studio" element={<Protected><Studio /></Protected>} />
      <Route path="/runs/:id" element={<Protected><RunLive /></Protected>} />
      <Route path="/memory" element={<Protected><Memory /></Protected>} />
      <Route path="/settings" element={<Protected><Settings /></Protected>} />
      <Route path="/admin" element={<Protected><Admin /></Protected>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
