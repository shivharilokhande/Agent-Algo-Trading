import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, MemoryOut, RunOut } from "../api";

export default function Dashboard() {
  const nav = useNavigate();
  const [runs, setRuns] = useState<RunOut[]>([]);
  const [pending, setPending] = useState<MemoryOut[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [memStats, setMemStats] = useState<any>(null);
  const [announcements, setAnnouncements] = useState<{ id: string; text: string }[]>([]);

  useEffect(() => {
    api.get<{ id: string; text: string }[]>("/api/announcements").then((list) => {
      setAnnouncements(list.filter((a) => {
        try { return !localStorage.getItem(`agentalgo_dismissed_${a.id}`); } catch { return true; }
      }));
    }).catch(() => {});
    api.get<RunOut[]>("/api/runs?limit=100").then(setRuns).catch(() => {});
    api.get<MemoryOut[]>("/api/memory?status=pending").then(setPending).catch(() => {});
    api.get("/api/health").then(setHealth).catch(() => {});
    api.get("/api/memory/stats").then(setMemStats).catch(() => {});
  }, []);

  const doneRuns = runs.filter((r) => r.status === "done");
  const tokens = runs.reduce((a, r) => a + (r.stats.tokens_in ?? 0) + (r.stats.tokens_out ?? 0), 0);
  const llmCalls = runs.reduce((a, r) => a + (r.stats.llm_calls ?? 0), 0);

  return (
    <div>
      <h1>Dashboard</h1>
      <p className="page-sub">What your agent teams have been analyzing, and what it cost in tokens.</p>

      {announcements.map((a) => (
        <div key={a.id} className="card" style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
          <span>📣 {a.text}</span>
          <button className="secondary small" onClick={() => {
            try { localStorage.setItem(`agentalgo_dismissed_${a.id}`, "1"); } catch { /* ignore */ }
            setAnnouncements(announcements.filter((x) => x.id !== a.id));
          }}>Dismiss</button>
        </div>
      ))}

      <div className="statrow">
        <div className="statcard">
          <div className="k">Analyses</div>
          <div className="v red">{runs.length}</div>
          <div className="s">{doneRuns.length} completed</div>
        </div>
        <div className="statcard">
          <div className="k">LLM calls</div>
          <div className="v">{llmCalls}</div>
          <div className="s">across all runs</div>
        </div>
        <div className="statcard">
          <div className="k">Tokens used</div>
          <div className="v">{(tokens / 1000).toFixed(1)}k</div>
          <div className="s">in + out</div>
        </div>
        <div className="statcard">
          <div className="k">Pending outcomes</div>
          <div className="v">{pending.length}</div>
          <div className="s">awaiting resolution</div>
        </div>
        <div className="statcard">
          <div className="k">Decisions resolved</div>
          <div className="v green">{memStats?.resolved ?? 0}</div>
          <div className="s">with realized alpha</div>
        </div>
      </div>
      {health && !health.engine_available && (
        <div className="card warn-card">
          <b>Demo mode active.</b> The TradingAgents engine is not installed on this server —
          runs use simulated agents. Install the engine and add an LLM key in{" "}
          <Link to="/settings">Settings</Link> for real analyses.
        </div>
      )}
      <div className="row" style={{ marginBottom: 16 }}>
        <button onClick={() => nav("/new")}>+ New Analysis</button>
        {runs[0] && (
          <button className="secondary" onClick={() => nav(`/new?again=${runs[0].id}`)}>
            Analyze {runs[0].ticker} again
          </button>
        )}
      </div>

      <div className="card">
        <h3>Recent runs</h3>
        {runs.length === 0 ? (
          <p className="muted">No runs yet — start your first analysis.</p>
        ) : (
          <table>
            <thead><tr><th>Ticker</th><th>Date</th><th>Mode</th><th>Status</th><th>Rating</th><th></th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td><b>{r.ticker}</b></td>
                  <td>{r.trade_date}</td>
                  <td className="muted">{r.mode}</td>
                  <td><span className={`pill ${r.status}`}>{r.status}</span></td>
                  <td>{r.rating && <span className={`rating ${r.rating}`}>{r.rating}</span>}</td>
                  <td><Link to={`/runs/${r.id}`}>open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>Pending decision-log resolutions</h3>
        {pending.length === 0 ? (
          <p className="muted">Nothing pending — completed runs appear here until their market outcome is resolved.</p>
        ) : (
          <p>
            {pending.length} decision(s) awaiting outcome resolution.{" "}
            <Link to="/memory">Resolve in Memory →</Link>
          </p>
        )}
      </div>
    </div>
  );
}
