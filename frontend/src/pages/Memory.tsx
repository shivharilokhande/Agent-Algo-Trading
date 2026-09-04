import { useEffect, useState } from "react";
import { api, MemoryOut } from "../api";

export default function Memory() {
  const [entries, setEntries] = useState<MemoryOut[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [desk, setDesk] = useState<any[]>([]);
  const [busy, setBusy] = useState<string>("");
  const [err, setErr] = useState("");

  async function load() {
    setEntries(await api.get<MemoryOut[]>("/api/memory"));
    setStats(await api.get("/api/memory/stats"));
    setDesk(await api.get<any[]>("/api/desk-review").catch(() => []) as any[]);
  }
  useEffect(() => { load(); }, []);

  async function resolve(id: string) {
    setBusy(id); setErr("");
    try {
      await api.post(`/api/memory/${id}/resolve`);
      await load();
    } catch (ex: any) {
      setErr(ex.message);
    } finally {
      setBusy("");
    }
  }

  const pct = (v: number | null) => (v === null || v === undefined ? "—" : (v * 100).toFixed(1) + "%");

  return (
    <div>
      <h1>Decision Log</h1>
      <p className="muted">
        Every completed run appends a pending entry. Resolving fetches the realized return and
        alpha vs. the run's benchmark, then writes a reflection that future runs read as context.
      </p>

      {stats && (
        <div className="card">
          <div className="grid4">
            <div className="stat"><div className="v">{stats.resolved}</div><div className="l">Resolved</div></div>
            <div className="stat"><div className="v">{stats.pending}</div><div className="l">Pending</div></div>
            {Object.entries(stats.by_rating as Record<string, any>).slice(0, 2).map(([r, b]) => (
              <div className="stat" key={r}>
                <div className="v">{b.hit_rate === null ? "—" : (b.hit_rate * 100).toFixed(0) + "%"}</div>
                <div className="l">{r} hit rate ({b.count})</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {err && <div className="error-box">{err}</div>}

      {desk.length > 0 && (
        <div className="card">
          <h3>Desk Review <span className="muted">— agents graded against realized alpha (ai-fund-style KPI review)</span></h3>
          <table>
            <thead><tr><th>Agent</th><th>Graded calls</th><th>Hit rate</th><th>Avg alpha</th><th>Bias (bull/bear/hold)</th><th>Verdict</th></tr></thead>
            <tbody>
              {desk.map((a) => (
                <tr key={a.agent}>
                  <td><b>{a.agent}</b></td>
                  <td>{a.graded}</td>
                  <td className="mono" style={{ color: a.hit_rate >= 0.5 ? "var(--green)" : "var(--red)" }}>
                    {(a.hit_rate * 100).toFixed(0)}%
                  </td>
                  <td className="mono">{(a.avg_alpha_when_called * 100).toFixed(2)}%</td>
                  <td className="muted">{a.bias.bullish}/{a.bias.bearish}/{a.bias.hold}</td>
                  <td><span className={`pill ${a.verdict === "ok" ? "done" : a.verdict === "watch" ? "interrupted" : "failed"}`}>
                    {a.verdict}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted" style={{ marginBottom: 0 }}>
            "bench" agents (≥5 calls, &lt;40% hit rate) are candidates for a persona rewrite in Agent Studio.
          </p>
        </div>
      )}

      <div className="card">
        <table>
          <thead>
            <tr><th>Ticker</th><th>Trade date</th><th>Rating</th><th>Status</th>
              <th>Raw return</th><th>Alpha</th><th>Reflection</th><th></th></tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td><b>{e.ticker}</b></td>
                <td>{e.trade_date}</td>
                <td><span className={`rating ${e.rating}`}>{e.rating}</span></td>
                <td><span className={`pill ${e.status}`}>{e.status}</span></td>
                <td className={e.raw_return !== null && e.raw_return < 0 ? "mono" : "mono"}
                  style={{ color: e.raw_return === null ? undefined : e.raw_return >= 0 ? "var(--green)" : "var(--red)" }}>
                  {pct(e.raw_return)}
                </td>
                <td className="mono"
                  style={{ color: e.alpha === null ? undefined : e.alpha >= 0 ? "var(--green)" : "var(--red)" }}>
                  {pct(e.alpha)} <span className="muted">vs {e.benchmark}</span>
                </td>
                <td style={{ maxWidth: 380 }} className="muted">{e.reflection || e.summary.slice(0, 120)}</td>
                <td>
                  {e.status === "pending" && (
                    <button className="small" disabled={busy === e.id} onClick={() => resolve(e.id)}>
                      {busy === e.id ? "…" : "Resolve now"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {entries.length === 0 && <tr><td colSpan={8} className="muted">No decisions logged yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
