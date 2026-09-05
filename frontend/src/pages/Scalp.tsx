import { useEffect, useState } from "react";
import { api } from "../api";

type Signal = {
  id: string; symbol: string; rule: string; direction: string; instrument: string;
  simulated: boolean; created_at: string; ep?: number; sl?: number; tp?: number;
  rr?: number; delta?: number; spot?: number; why?: string; time_stop_min?: number;
  sizing?: any;
};

type Status = {
  enabled: boolean; symbols: string[]; market_open: boolean;
  theta_cutoff: boolean; poll_seconds: number; bias: Record<string, string | null>;
};

const ALL_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY"];
const inr = (v: number) => "₹" + Number(v).toLocaleString("en-IN");

export default function Scalp() {
  const [status, setStatus] = useState<Status | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [cfg, setCfg] = useState<any>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function load() {
    try {
      setStatus(await api.get<Status>("/api/scalp/status"));
      setSignals(await api.get<Signal[]>("/api/scalp/signals"));
      const s = await api.get<any>("/api/settings");
      setCfg(s.config || {});
    } catch (ex: any) { setErr(ex.message); }
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 15000); // live refresh
    return () => clearInterval(t);
  }, []);

  async function saveCfg(patch: any) {
    setBusy(true); setErr("");
    try {
      const next = { ...cfg, ...patch };
      await api.put("/api/settings", { config: next });
      setCfg(next);
      setStatus(await api.get<Status>("/api/scalp/status"));
    } catch (ex: any) { setErr(ex.message); } finally { setBusy(false); }
  }

  async function simulate() {
    setBusy(true); setErr("");
    try {
      await api.post("/api/scalp/simulate");
      await load();
    } catch (ex: any) { setErr(ex.message); } finally { setBusy(false); }
  }

  const symbols: string[] = cfg.scalp_symbols || ["NIFTY"];

  return (
    <div>
      <h1>Scalp Mode</h1>
      <p className="muted">
        Fast lane: rule-based signals (ORB, VWAP reclaim, OI-wall reject) computed every{" "}
        {status?.poll_seconds ?? 45}s from live 1-minute data — filtered by the day's agent bias,
        theta-aware (no new signals after 14:30 IST), tight brackets with a {""}
        20-minute time stop. Research signals only — you place every order yourself.
      </p>

      {err && <div className="error-box">{err}</div>}

      <div className="card">
        <div style={{ display: "flex", gap: 24, alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 600 }}>
            <input type="checkbox" checked={!!cfg.scalp_enabled} disabled={busy}
              onChange={(e) => saveCfg({ scalp_enabled: e.target.checked })} />
            Scalp engine {cfg.scalp_enabled ? "ON" : "OFF"}
          </label>
          <span>
            {ALL_SYMBOLS.map((s) => (
              <label key={s} style={{ marginRight: 14 }}>
                <input type="checkbox" checked={symbols.includes(s)} disabled={busy}
                  onChange={(e) => saveCfg({
                    scalp_symbols: e.target.checked
                      ? [...symbols, s] : symbols.filter((x) => x !== s),
                  })} /> {s}
              </label>
            ))}
          </span>
          <label>
            Scalp risk %/trade{" "}
            <input type="number" step="0.1" min="0.1" max="5" style={{ width: 70 }}
              defaultValue={cfg.scalp_risk_pct ?? ""}
              placeholder="½ of swing"
              onBlur={(e) => e.target.value && saveCfg({ scalp_risk_pct: Number(e.target.value) })} />
          </label>
          <button className="small" disabled={busy} onClick={simulate}>Test signal (simulated)</button>
        </div>
        {status && (
          <p className="muted" style={{ marginBottom: 0 }}>
            Market {status.market_open ? "OPEN" : "closed"}
            {status.theta_cutoff && " · past 14:30 theta cutoff — no new signals"}
            {" · bias: "}
            {Object.entries(status.bias).map(([s, b]) => `${s}=${b ?? "none"}`).join(", ")}
            {" (from your latest engine run — run the F&O Desk each morning to set it)"}
          </p>
        )}
      </div>

      <div className="card">
        <h3>Signals</h3>
        <table>
          <thead>
            <tr><th>Time</th><th>Rule</th><th>Buy</th><th>Entry</th><th>Stop</th>
              <th>Target</th><th>Size</th><th>Why</th></tr>
          </thead>
          <tbody>
            {signals.map((s) => (
              <tr key={s.id} style={s.simulated ? { opacity: 0.75 } : undefined}>
                <td className="mono">{new Date(s.created_at).toLocaleTimeString("en-IN", { hour12: false })}
                  {s.simulated && <div><span className="pill failed" style={{ fontSize: 10 }}>SIMULATED</span></div>}
                </td>
                <td><b>{s.rule}</b></td>
                <td><b>{s.instrument}</b>{s.delta != null && <div className="muted">Δ {Number(s.delta).toFixed(2)}</div>}</td>
                <td className="mono">≈ {inr(s.ep!)}</td>
                <td className="mono" style={{ color: "var(--red)" }}>{inr(s.sl!)}</td>
                <td className="mono" style={{ color: "var(--green)" }}>{inr(s.tp!)} <span className="muted">1:{s.rr}</span></td>
                <td style={{ fontSize: 12 }}>
                  {s.sizing?.lots
                    ? <><b>{s.sizing.lots} lot{s.sizing.lots > 1 ? "s" : ""}</b>
                      <div style={{ color: "var(--red)" }}>max loss {inr(s.sizing.max_loss)}</div>
                      {s.sizing.profit_tp && <div style={{ color: "var(--green)" }}>profit {inr(s.sizing.profit_tp)}</div>}</>
                    : <span className="muted">{s.sizing?.note || "—"}</span>}
                </td>
                <td className="muted" style={{ maxWidth: 320, fontSize: 12 }}>
                  {s.why} · exit in {s.time_stop_min} min if flat
                </td>
              </tr>
            ))}
            {signals.length === 0 && (
              <tr><td colSpan={8} className="muted">
                No signals yet. Turn the engine ON — during market hours it scans every{" "}
                {status?.poll_seconds ?? 45} seconds. Use "Test signal" to preview the flow now.
              </td></tr>
            )}
          </tbody>
        </table>
        <p className="disclaimer" style={{ padding: 0, marginTop: 8 }}>
          Scalping long options is theta-negative and fast — take only signals aligned with your own
          read, honour the stop and the time stop. Signals also arrive as Mac notifications.
          Research, not advice.
        </p>
      </div>
    </div>
  );
}
