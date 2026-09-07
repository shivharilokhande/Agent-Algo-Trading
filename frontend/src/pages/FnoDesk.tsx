import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, RunOut, istToday } from "../api";

const INDICES = [
  { alias: "NIFTY", label: "NIFTY 50" },
  { alias: "BANKNIFTY", label: "BANK NIFTY" },
  { alias: "FINNIFTY", label: "FIN NIFTY" },
];

export default function FnoDesk() {
  const nav = useNavigate();
  const [snaps, setSnaps] = useState<Record<string, any>>({});
  const [stock, setStock] = useState("");
  const [mode, setMode] = useState<"engine" | "demo">("engine");
  const [depth, setDepth] = useState(1);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [fnoRuns, setFnoRuns] = useState<RunOut[]>([]);

  useEffect(() => {
    INDICES.forEach(({ alias }) => {
      api.get<any>(`/api/catalog/fno/${alias}`)
        .then((s) => setSnaps((x) => ({ ...x, [alias]: s })))
        .catch((e) => setSnaps((x) => ({ ...x, [alias]: { error: e.message } })));
    });
    api.get<RunOut[]>("/api/runs?limit=100").then((runs) =>
      setFnoRuns(runs.filter((r) => (r.config as any).fno_mode))
    ).catch(() => {});
  }, []);

  async function analyze(ticker: string) {
    setBusy(ticker); setMsg(null);
    try {
      const run = await api.post<RunOut>("/api/runs", {
        ticker,
        trade_date: istToday(),
        research_depth: depth,
        mode,
        fno_mode: true,
        analysts: ["market", "social", "news", "fundamentals"],  // server filters for indices
        llm_provider: mode === "engine" ? "openai_compatible" : "demo",
        quick_think_llm: mode === "engine" ? "cowork" : "",
        deep_think_llm: mode === "engine" ? "cowork" : "",
      });
      nav(`/runs/${run.id}`);
    } catch (ex: any) {
      setMsg({ kind: "error", text: ex.message });
    } finally { setBusy(""); }
  }

  const fmt = (n: any) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("en-IN"));

  return (
    <div>
      <h1>F&O Desk</h1>
      <p className="page-sub">
        Derivatives-first analysis: the full agent pipeline (analysts → research debate → trader →
        risk → portfolio manager) plus live NSE option-chain positioning, ending in a concrete
        option trade plan — strike & premium, entry, stop loss, targets, risk:reward.
      </p>
      {msg && <div className={msg.kind === "ok" ? "ok-box" : "error-box"}>{msg.text}</div>}

      <div className="row" style={{ marginBottom: 14 }}>
        <div className="checkbox-row">
          <input type="radio" id="fe" checked={mode === "engine"} onChange={() => setMode("engine")} />
          <label htmlFor="fe" style={{ margin: 0, color: "var(--text)" }}>
            <b>Live engine</b> <span className="muted">— Cowork bridge, LLM-written trade plan</span>
          </label>
        </div>
        <div className="checkbox-row">
          <input type="radio" id="fd" checked={mode === "demo"} onChange={() => setMode("demo")} />
          <label htmlFor="fd" style={{ margin: 0, color: "var(--text)" }}>
            <b>Demo</b> <span className="muted">— simulated agents, real chain, rule-based plan</span>
          </label>
        </div>
        <select value={depth} onChange={(e) => setDepth(+e.target.value)} style={{ width: 130 }}>
          <option value={1}>Shallow</option><option value={3}>Medium</option><option value={5}>Deep</option>
        </select>
      </div>

      <div className="grid3">
        {INDICES.map(({ alias, label }) => {
          const s = snaps[alias];
          return (
            <div className="card" key={alias} style={{ marginBottom: 0 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h3 style={{ margin: 0 }}>{label}</h3>
                <button className="small" disabled={busy === alias} onClick={() => analyze(alias)}>
                  {busy === alias ? "Starting…" : "▶ Analyze"}
                </button>
              </div>
              {!s && <p className="muted">Loading live chain…</p>}
              {s?.error && <p className="muted" style={{ fontSize: 12 }}>Chain unavailable: {s.error.slice(0, 60)}</p>}
              {s && !s.error && (
                <table style={{ marginTop: 10 }}>
                  <tbody>
                    <tr><td className="muted">Spot</td><td className="mono"><b>{fmt(s.spot)}</b></td></tr>
                    <tr><td className="muted">PCR (OI)</td><td className="mono">{s.pcr}</td></tr>
                    <tr><td className="muted">Max pain</td><td className="mono">{fmt(s.max_pain)}</td></tr>
                    <tr><td className="muted">OI res / sup</td>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {fmt(s.resistance_strikes?.[0]?.strike)} / {fmt(s.support_strikes?.[0]?.strike)}
                      </td></tr>
                    {s.india_vix != null && <tr><td className="muted">India VIX</td><td className="mono">{s.india_vix}</td></tr>}
                    <tr><td className="muted">Expiry</td><td className="mono" style={{ fontSize: 12 }}>{s.expiry}</td></tr>
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Stock F&O <span className="muted">(any NSE F&O-listed stock)</span></h3>
        <div className="row">
          <input placeholder="e.g. RELIANCE.NS, HDFCBANK.NS, TCS.NS" value={stock}
            style={{ width: 280 }}
            onChange={(e) => setStock(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && stock.trim() && analyze(stock.trim())} />
          <button disabled={!stock.trim() || busy === stock.trim()} onClick={() => analyze(stock.trim())}>
            {busy === stock.trim() ? "Starting…" : "▶ Analyze F&O"}
          </button>
          <span className="muted">The .NS suffix is required for stocks.</span>
        </div>
      </div>

      <div className="card">
        <h3>F&O analyses</h3>
        <table>
          <thead><tr><th>Instrument</th><th>Date</th><th>Mode</th><th>Status</th><th>Rating</th><th></th></tr></thead>
          <tbody>
            {fnoRuns.map((r) => (
              <tr key={r.id}>
                <td><b>{r.ticker}</b></td>
                <td>{r.trade_date}</td>
                <td className="muted">{r.mode}</td>
                <td><span className={`pill ${r.status}`}>{r.status}</span></td>
                <td>{r.rating && <span className={`rating ${r.rating}`}>{r.rating}</span>}</td>
                <td><Link to={`/runs/${r.id}`}>open</Link></td>
              </tr>
            ))}
            {fnoRuns.length === 0 && <tr><td colSpan={6} className="muted">
              No F&O analyses yet — hit Analyze on an index above.</td></tr>}
          </tbody>
        </table>
      </div>

      <p className="disclaimer" style={{ padding: 0 }}>
        Options are leveraged instruments; premiums can go to zero. Trade plans are research output
        from simulated/LLM agents — verify lot sizes, liquidity, and margins with your broker. Not
        financial advice.
      </p>
    </div>
  );
}
