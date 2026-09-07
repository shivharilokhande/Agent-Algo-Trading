import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, istToday } from "../api";

export default function Ensemble() {
  const [ensembles, setEnsembles] = useState<any[]>([]);
  const [board, setBoard] = useState<any[]>([]);
  const [ticker, setTicker] = useState("NVDA");
  const [labels, setLabels] = useState("Conservative stack, Balanced stack, Aggressive stack");
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setEnsembles(await api.get("/api/ensembles"));
    setBoard(await api.get("/api/scoreboard"));
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);

  async function create() {
    setBusy(true); setMsg(null);
    try {
      const stacks = labels.split(",").map((l) => l.trim()).filter(Boolean).slice(0, 3)
        .map((label) => ({ label, mode: "demo" }));
      if (stacks.length < 2) throw new Error("Give at least two stack labels");
      await api.post("/api/ensembles", {
        ticker: ticker.trim(), trade_date: istToday(),
        research_depth: 1, stacks,
      });
      setMsg({ kind: "ok", text: "Ensemble launched — stacks run in parallel; the meta-judge rules when all finish." });
      await load();
    } catch (ex: any) {
      setMsg({ kind: "error", text: ex.message });
    } finally { setBusy(false); }
  }

  const verdictPill = (v: string) =>
    v === "unanimous" ? "done" : v === "majority" ? "running" : "interrupted";

  return (
    <div>
      <h1>Ensemble</h1>
      <p className="page-sub">One analysis, several model stacks in parallel — a meta-judge compares the decisions and surfaces dissent.</p>
      {msg && <div className={msg.kind === "ok" ? "ok-box" : "error-box"}>{msg.text}</div>}

      <div className="card">
        <div className="row">
          <input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} style={{ width: 140 }} />
          <input value={labels} onChange={(e) => setLabels(e.target.value)} style={{ flex: 1, minWidth: 280 }}
            placeholder="Stack labels, comma-separated (2–3)" />
          <button disabled={busy} onClick={create}>{busy ? "Launching…" : "▶ Run ensemble"}</button>
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>
          Demo stacks vary by seed; with engine keys each stack can be a different provider/model pair.
        </p>
      </div>

      {ensembles.map((e) => (
        <div className="card" key={e.id}>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <h3 style={{ margin: 0 }}>{e.ticker} · {e.trade_date}</h3>
            {e.consensus?.status === "done"
              ? <span className={`pill ${verdictPill(e.consensus.verdict)}`}>{e.consensus.verdict} · leans {e.consensus.lean}</span>
              : <span className="pill running">running…</span>}
          </div>
          {e.consensus?.status === "done" && <p><b>Meta-judge:</b> {e.consensus.headline}</p>}
          <table>
            <thead><tr><th>Stack</th><th>Status</th><th>Rating</th><th>Tokens</th><th></th></tr></thead>
            <tbody>
              {e.runs.map((r: any) => (
                <tr key={r.id}>
                  <td><b>{r.label}</b></td>
                  <td><span className={`pill ${r.status}`}>{r.status}</span></td>
                  <td>{r.rating && <span className={`rating ${r.rating}`}>{r.rating}</span>}</td>
                  <td className="muted">{(((r.stats.tokens_in ?? 0) + (r.stats.tokens_out ?? 0)) / 1000).toFixed(1)}k</td>
                  <td><Link to={`/runs/${r.id}`}>open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      <div className="card">
        <h3>Provider scoreboard <span className="muted">(realized alpha from resolved decisions)</span></h3>
        <table>
          <thead><tr><th>Stack</th><th>Decisions</th><th>Avg alpha</th><th>Hit rate</th></tr></thead>
          <tbody>
            {board.map((b) => (
              <tr key={b.stack}>
                <td><b>{b.stack}</b></td>
                <td>{b.decisions}</td>
                <td className="mono" style={{ color: b.avg_alpha >= 0 ? "var(--green)" : "var(--red)" }}>
                  {(b.avg_alpha * 100).toFixed(2)}%
                </td>
                <td className="mono">{(b.hit_rate * 100).toFixed(0)}%</td>
              </tr>
            ))}
            {board.length === 0 && <tr><td colSpan={4} className="muted">
              Resolve decisions in Memory to populate the scoreboard.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
