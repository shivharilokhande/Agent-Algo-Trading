import { useEffect, useState } from "react";
import { api, fmtIst } from "../api";

interface Stats {
  users: number; admins: number; runs_total: number;
  runs_by_status: Record<string, number>; runs_by_mode: Record<string, number>;
  ratings: Record<string, number>; tokens_in: number; tokens_out: number;
  llm_calls: number; keys_stored: number; memory_entries: number;
  memory_resolved: number; top_tickers: { ticker: string; runs: number }[];
}
interface AdminUser {
  id: string; email: string; is_admin: boolean; created_at: string;
  runs: number; keys: number; last_run: string | null;
}

export default function Admin() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [err, setErr] = useState("");

  async function load() {
    try {
      setStats(await api.get<Stats>("/api/admin/stats"));
      setUsers(await api.get<AdminUser[]>("/api/admin/users"));
    } catch (ex: any) {
      setErr(ex.message);
    }
  }
  useEffect(() => { load(); }, []);

  async function toggleAdmin(id: string) {
    await api.post(`/api/admin/users/${id}/toggle-admin`).catch((e) => setErr(e.message));
    load();
  }
  async function removeUser(u: AdminUser) {
    if (!confirm(`Delete ${u.email} and ALL their data (${u.runs} runs)?`)) return;
    await api.del(`/api/admin/users/${u.id}`).catch((e) => setErr(e.message));
    load();
  }

  if (err) return <div className="error-box">{err}</div>;
  if (!stats) return <p className="muted">Loading…</p>;
  const done = stats.runs_by_status["done"] ?? 0;
  const successRate = stats.runs_total ? Math.round((done / stats.runs_total) * 100) : 0;

  return (
    <div>
      <h1>Admin</h1>
      <p className="page-sub">Platform overview — every account, run, and token across AgentAlgo.</p>

      <div className="statrow">
        <div className="statcard">
          <div className="k">Users</div>
          <div className="v">{stats.users}</div>
          <div className="s">{stats.admins} admin(s)</div>
        </div>
        <div className="statcard">
          <div className="k">Analyses run</div>
          <div className="v red">{stats.runs_total}</div>
          <div className="s">{done} completed · {successRate}% success</div>
        </div>
        <div className="statcard">
          <div className="k">LLM calls</div>
          <div className="v">{stats.llm_calls}</div>
          <div className="s">{((stats.tokens_in + stats.tokens_out) / 1000).toFixed(1)}k tokens total</div>
        </div>
        <div className="statcard">
          <div className="k">Keys stored</div>
          <div className="v">{stats.keys_stored}</div>
          <div className="s">encrypted at rest</div>
        </div>
        <div className="statcard">
          <div className="k">Decision log</div>
          <div className="v green">{stats.memory_resolved}</div>
          <div className="s">of {stats.memory_entries} entries resolved</div>
        </div>
      </div>

      <div className="grid2">
        <div className="card">
          <h3>Ratings issued</h3>
          <table>
            <thead><tr><th>Rating</th><th>Count</th></tr></thead>
            <tbody>
              {["Buy", "Overweight", "Hold", "Underweight", "Sell", "REVIEW"].map((r) =>
                stats.ratings[r] ? (
                  <tr key={r}><td><span className={`rating ${r}`}>{r}</span></td><td>{stats.ratings[r]}</td></tr>
                ) : null
              )}
              {Object.keys(stats.ratings).length === 0 && (
                <tr><td colSpan={2} className="muted">No completed runs yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h3>Top tickers</h3>
          <table>
            <thead><tr><th>Ticker</th><th>Runs</th></tr></thead>
            <tbody>
              {stats.top_tickers.map((t) => (
                <tr key={t.ticker}><td><b>{t.ticker}</b></td><td>{t.runs}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h3>Users</h3>
        <table>
          <thead>
            <tr><th>Email</th><th>Role</th><th>Runs</th><th>Keys</th><th>Last run</th><th>Joined</th><th></th></tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td><b>{u.email}</b></td>
                <td>{u.is_admin ? <span className="pill admin">admin</span> : <span className="pill pending">user</span>}</td>
                <td>{u.runs}</td>
                <td>{u.keys}</td>
                <td className="muted">{u.last_run ? fmtIst(u.last_run) : "—"}</td>
                <td className="muted">{fmtIst(u.created_at, { dateStyle: "medium" })}</td>
                <td className="row">
                  <button className="secondary small" onClick={() => toggleAdmin(u.id)}>
                    {u.is_admin ? "Demote" : "Make admin"}
                  </button>
                  <button className="danger small" onClick={() => removeUser(u)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
