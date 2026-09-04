import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function Automations() {
  const [schedules, setSchedules] = useState<any[]>([]);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  // new-schedule form
  const [name, setName] = useState("");
  const [tickers, setTickers] = useState("");
  const [cadence, setCadence] = useState("weekdays");
  const [weekday, setWeekday] = useState(0);
  const [hour, setHour] = useState(7);
  const [depth, setDepth] = useState(1);

  async function load() {
    setSchedules(await api.get("/api/schedules"));
    setAlerts(await api.get("/api/alerts"));
  }
  useEffect(() => { load(); }, []);

  async function create() {
    setMsg(null);
    try {
      await api.post("/api/schedules", {
        name: name.trim(),
        tickers: tickers.split(/[,\s]+/).filter(Boolean),
        cadence, weekday, hour,
        config: { research_depth: depth, mode: "demo" },
      });
      setName(""); setTickers("");
      await load();
    } catch (ex: any) {
      setMsg({ kind: "error", text: ex.message });
    }
  }

  async function markAllRead() {
    await api.post("/api/alerts/read");
    await load();
    window.dispatchEvent(new Event("agentalgo-alerts-read"));
  }

  return (
    <div>
      <h1>Automations</h1>
      <p className="page-sub">Scheduled analyses and rating-change alerts.</p>
      {msg && <div className={msg.kind === "ok" ? "ok-box" : "error-box"}>{msg.text}</div>}

      <div className="card">
        <h3>New schedule</h3>
        <div className="row">
          <input placeholder="Name" value={name} style={{ width: 150 }} onChange={(e) => setName(e.target.value)} />
          <input placeholder="Tickers (NVDA, AAPL…)" value={tickers} style={{ flex: 1, minWidth: 200 }}
            onChange={(e) => setTickers(e.target.value.toUpperCase())} />
          <select value={cadence} onChange={(e) => setCadence(e.target.value)} style={{ width: 120 }}>
            <option value="daily">Daily</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekly">Weekly</option>
          </select>
          {cadence === "weekly" && (
            <select value={weekday} onChange={(e) => setWeekday(+e.target.value)} style={{ width: 90 }}>
              {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
            </select>
          )}
          <select value={hour} onChange={(e) => setHour(+e.target.value)} style={{ width: 110 }}>
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
          </select>
          <select value={depth} onChange={(e) => setDepth(+e.target.value)} style={{ width: 110 }}>
            <option value={1}>Shallow</option><option value={3}>Medium</option><option value={5}>Deep</option>
          </select>
          <button disabled={!name.trim() || !tickers.trim()} onClick={create}>Add</button>
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>Times are server-local. Scheduled runs queue automatically and appear in Run History.</p>
      </div>

      <div className="card">
        <h3>Schedules</h3>
        <table>
          <thead><tr><th>Name</th><th>Tickers</th><th>Cadence</th><th>Time</th><th>Last fired</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {schedules.map((s) => (
              <tr key={s.id}>
                <td><b>{s.name}</b></td>
                <td className="muted">{s.tickers.join(", ")}</td>
                <td>{s.cadence}{s.cadence === "weekly" ? ` (${DAYS[s.weekday]})` : ""}</td>
                <td>{String(s.hour).padStart(2, "0")}:00</td>
                <td className="muted">{s.last_fired_date || "never"}</td>
                <td><span className={`pill ${s.enabled ? "done" : "pending"}`}>{s.enabled ? "enabled" : "paused"}</span></td>
                <td className="row">
                  <button className="secondary small" onClick={async () => { await api.post(`/api/schedules/${s.id}/toggle`); load(); }}>
                    {s.enabled ? "Pause" : "Enable"}
                  </button>
                  <button className="danger small" onClick={async () => { await api.del(`/api/schedules/${s.id}`); load(); }}>Delete</button>
                </td>
              </tr>
            ))}
            {schedules.length === 0 && <tr><td colSpan={7} className="muted">No schedules yet.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
          <h3 style={{ margin: 0 }}>Alerts</h3>
          {alerts.some((a) => !a.read) && <button className="secondary small" onClick={markAllRead}>Mark all read</button>}
        </div>
        <table>
          <thead><tr><th>When</th><th>Ticker</th><th>Type</th><th>Message</th><th></th></tr></thead>
          <tbody>
            {alerts.map((a) => (
              <tr key={a.id} style={{ opacity: a.read ? 0.55 : 1 }}>
                <td className="muted">{new Date(a.created_at).toLocaleString()}</td>
                <td><b>{a.ticker}</b></td>
                <td><span className={`pill ${a.type === "review" ? "interrupted" : "running"}`}>{a.type.replace("_", " ")}</span></td>
                <td>{a.message}</td>
                <td>{a.run_id && <Link to={`/runs/${a.run_id}`}>open</Link>}</td>
              </tr>
            ))}
            {alerts.length === 0 && <tr><td colSpan={5} className="muted">No alerts yet — they appear when a ticker's rating changes tier or a run needs REVIEW.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
