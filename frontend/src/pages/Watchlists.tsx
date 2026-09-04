import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

interface WL {
  id: string; name: string; tickers: string[];
  latest: Record<string, { run_id: string; rating: string | null; trade_date: string }>;
}

export default function Watchlists() {
  const [lists, setLists] = useState<WL[]>([]);
  const [name, setName] = useState("");
  const [tickers, setTickers] = useState("");
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [busy, setBusy] = useState("");

  async function load() {
    setLists(await api.get<WL[]>("/api/watchlists"));
  }
  useEffect(() => { load(); }, []);

  async function create() {
    setMsg(null);
    try {
      await api.post("/api/watchlists", {
        name: name.trim(),
        tickers: tickers.split(/[,\s]+/).filter(Boolean),
      });
      setName(""); setTickers("");
      await load();
    } catch (ex: any) {
      setMsg({ kind: "error", text: ex.message });
    }
  }

  async function runAll(w: WL) {
    setBusy(w.id); setMsg(null);
    try {
      const r = await api.post<any>(`/api/watchlists/${w.id}/run`, { research_depth: 1, mode: "demo" });
      const queued = r.created.filter((c: any) => c.status === "queued").length;
      setMsg({
        kind: "ok",
        text: `Started ${r.created.length - queued} run(s), queued ${queued} — they start automatically as slots free. Watch them in Run History.`,
      });
      setTimeout(load, 4000);
    } catch (ex: any) {
      setMsg({ kind: "error", text: ex.message });
    } finally { setBusy(""); }
  }

  return (
    <div>
      <h1>Watchlists</h1>
      <p className="page-sub">Run the full agent pipeline across a list of tickers in one click.</p>
      {msg && <div className={msg.kind === "ok" ? "ok-box" : "error-box"}>{msg.text}</div>}

      <div className="card">
        <div className="row">
          <input placeholder="List name (e.g. Tech)" value={name} style={{ width: 180 }}
            onChange={(e) => setName(e.target.value)} />
          <input placeholder="Tickers: NVDA, MSFT, RELIANCE.NS, BTC-USD…" value={tickers}
            style={{ flex: 1, minWidth: 260 }} onChange={(e) => setTickers(e.target.value.toUpperCase())} />
          <button disabled={!name.trim() || !tickers.trim()} onClick={create}>Save watchlist</button>
        </div>
      </div>

      {lists.map((w) => (
        <div className="card" key={w.id}>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>{w.name} <span className="muted">({w.tickers.length})</span></h3>
            <div className="row">
              <button className="small" disabled={busy === w.id} onClick={() => runAll(w)}>
                {busy === w.id ? "Starting…" : "▶ Run all"}
              </button>
              <button className="danger small" onClick={async () => { await api.del(`/api/watchlists/${w.id}`); load(); }}>
                Delete
              </button>
            </div>
          </div>
          <table>
            <thead><tr><th>Ticker</th><th>Latest rating</th><th>As of</th><th></th></tr></thead>
            <tbody>
              {w.tickers.map((t) => {
                const l = w.latest[t];
                return (
                  <tr key={t}>
                    <td><b>{t}</b></td>
                    <td>{l?.rating ? <span className={`rating ${l.rating}`}>{l.rating}</span> : <span className="muted">not analyzed</span>}</td>
                    <td className="muted">{l?.trade_date ?? "—"}</td>
                    <td>{l && <Link to={`/runs/${l.run_id}`}>open run</Link>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
      {lists.length === 0 && <p className="muted">No watchlists yet.</p>}
    </div>
  );
}
