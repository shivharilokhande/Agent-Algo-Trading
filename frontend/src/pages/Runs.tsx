import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, RunOut } from "../api";

export default function Runs() {
  const [runs, setRuns] = useState<RunOut[]>([]);
  const [ticker, setTicker] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");

  async function load() {
    const q = new URLSearchParams();
    if (ticker.trim()) q.set("ticker", ticker.trim());
    if (status) q.set("status", status);
    if (search.trim()) q.set("q", search.trim());
    setRuns(await api.get<RunOut[]>(`/api/runs?${q}`));
  }
  useEffect(() => { load(); /* eslint-disable-line */ }, [status]);

  async function remove(id: string) {
    if (!confirm("Delete this run and its reports?")) return;
    await api.del(`/api/runs/${id}`);
    load();
  }

  return (
    <div>
      <h1>Run History</h1>
      <div className="row" style={{ marginBottom: 14 }}>
        <input placeholder="Filter by ticker…" value={ticker} style={{ width: 160 }}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && load()} />
        <input placeholder="Search reports & decisions…" value={search} style={{ width: 230 }}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()} />
        <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 160 }}>
          <option value="">All statuses</option>
          {["done", "running", "interrupted", "cancelled", "failed"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <button className="secondary" onClick={load}>Filter</button>
      </div>
      <div className="card">
        <table>
          <thead>
            <tr><th>Ticker</th><th>Trade date</th><th>Mode</th><th>Depth</th><th>Status</th>
              <th>Rating</th><th>Tokens</th><th>Duration</th><th></th></tr>
          </thead>
          <tbody>
            {runs.map((r) => {
              const dur = r.started_at && r.finished_at
                ? Math.round((+new Date(r.finished_at) - +new Date(r.started_at)) / 1000) + "s" : "—";
              const tokens = (r.stats.tokens_in ?? 0) + (r.stats.tokens_out ?? 0);
              return (
                <tr key={r.id}>
                  <td><b>{r.ticker}</b></td>
                  <td>{r.trade_date}</td>
                  <td className="muted">{r.mode}</td>
                  <td className="muted">{String(r.config.research_depth ?? "")}</td>
                  <td><span className={`pill ${r.status}`}>{r.status}</span></td>
                  <td>{r.rating && <span className={`rating ${r.rating}`}>{r.rating}</span>}</td>
                  <td className="muted">{tokens ? (tokens / 1000).toFixed(1) + "k" : "—"}</td>
                  <td className="muted">{dur}</td>
                  <td className="row">
                    <Link to={`/runs/${r.id}`}>open</Link>
                    <Link to={`/new?again=${r.id}`}>re-run</Link>
                    <a href="#" onClick={(e) => { e.preventDefault(); remove(r.id); }} style={{ color: "var(--red)" }}>✕</a>
                  </td>
                </tr>
              );
            })}
            {runs.length === 0 && <tr><td colSpan={9} className="muted">No runs match.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
