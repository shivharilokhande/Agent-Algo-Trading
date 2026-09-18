import { useEffect, useState } from "react";
import { api } from "../api";

const inr = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : "₹" + Number(v).toLocaleString("en-IN");

const pnlColor = (v: number | null | undefined) =>
  v == null ? undefined : v >= 0 ? "var(--green)" : "var(--red)";

type Account = "A" | "B" | "C";
const ACCOUNTS: { id: Account; label: string; blurb: string }[] = [
  { id: "A", label: "A · every signal",
    blurb: "A takes every signal — the unchanged go-live baseline." },
  { id: "B", label: "B · strike-chart checked",
    blurb: "B skips signals whose option premium already ran >15% off its 15-min low (EXT rows)." },
  { id: "C", label: "C · risk-guarded",
    blurb: "C runs A's signals behind guards: 2% risk, max 4 trades/day, −3% day stop, one WALL_REJECT per symbol per 30 min, ORB off, and on Hold / NO-TRADE days a wall touch must be fresh (≤2 bars) in a clean tape." },
];
// why C (or B) stood aside — shown on SKIP pills
const SKIP_WHY: Record<string, string> = {
  EXT: "premium already extended at signal time",
  RULE: "rule disabled in C (ORB: no live evidence yet)",
  DAY: "day loss stop hit (−3% of open-of-day equity)",
  MAX: "max 4 trades/day reached",
  CD: "symbol cooldown — another trade on this index in the last 30 min",
  REG: "range regime — stale wall touch or wide-OR / extended-EMA tape on a Hold day",
};

export default function PaperTrade() {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  const [capEdit, setCapEdit] = useState<string>("");
  const [riskEdit, setRiskEdit] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [account, setAccount] = useState<Account>("A");

  async function load(acct: Account = account) {
    try {
      const d = await api.get<any>(`/api/scalp/paper-trades?days=35&account=${acct}`);
      setData(d);
      setErr("");
      if (!loadedOnce && d?.summary) {  // seed the editors once, don't clobber typing
        setCapEdit(String(d.summary.base_capital ?? ""));
        setRiskEdit(String(d.summary.risk_pct ?? ""));
        setLoadedOnce(true);
      }
    } catch (ex: any) { setErr(ex.message); }
  }
  useEffect(() => {
    load(account);
    const t = setInterval(() => load(account), 15000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadedOnce, account]);

  const [exiting, setExiting] = useState<string | null>(null);
  async function manualExit(id: string) {
    if (!window.confirm("Exit this paper trade now at the live Kite bid? " +
        "It will be recorded as a MANUAL exit (not a rule outcome).")) return;
    setExiting(id); setErr("");
    try {
      await api.post(`/api/scalp/paper-trades/${id}/exit`, {});
      await load(account);
    } catch (ex: any) { setErr(ex.message); } finally { setExiting(null); }
  }

  const capOk = Number.isFinite(Number(capEdit)) && Number(capEdit) >= 10000;
  const riskOk = Number.isFinite(Number(riskEdit)) && Number(riskEdit) >= 0.1 && Number(riskEdit) <= 10;

  async function saveAccount() {
    if (!capOk || !riskOk) return;
    setSaving(true); setErr("");
    try {
      await api.put("/api/settings", { config: {
        paper_capital: Number(capEdit), paper_risk_pct: Number(riskEdit) } });
      await load();
    } catch (ex: any) { setErr(ex.message); } finally { setSaving(false); }
  }

  const s = data?.summary;
  const rows = data?.rows ?? [];

  return (
    <div className="scalp-page">
      <h1>Paper Trade</h1>
      <p className="muted">
        Every real scalp signal is executed here as a paper trade in three parallel accounts
        (same base capital, max {s?.max_concurrent ?? 2} open at once each) so the month-end
        A/B/C comparison can attribute every rupee to one filter.
        Entries use the real quoted premium; exits fill on the <b>real Kite bid</b> the moment
        SL / TP / the 20-minute stop triggers (marked "modeled" when the broker session was down).
        Exact Zerodha charges. No real orders — this is the 30-day evidence for the go-live decision.
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {ACCOUNTS.map((a) => (
          <button key={a.id} className={account === a.id ? undefined : "secondary"}
            onClick={() => setAccount(a.id)}>
            {a.label}
          </button>
        ))}
        <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>
          {ACCOUNTS.find((a) => a.id === account)?.blurb}
        </span>
      </div>

      {err && <div className="error-box">{err}</div>}

      {s && (
        <div className="card">
          <div style={{ display: "flex", gap: 28, flexWrap: "wrap", alignItems: "baseline" }}>
            <div><div style={{ fontSize: 22, fontWeight: 700, color: pnlColor(s.net_pnl) }}>
              {s.net_pnl >= 0 ? "+" : ""}{inr(s.net_pnl).replace("₹", "₹")}</div>
              <div className="l">Net P&L after {inr(s.total_charges)} charges</div></div>
            <div><div style={{ fontSize: 22, fontWeight: 700 }}>{inr(s.equity)}</div>
              <div className="l">Equity · base {inr(s.base_capital)}{s.reserved ? ` · ${inr(s.reserved)} in open trades` : ""}</div></div>
            <div><div style={{ fontSize: 22, fontWeight: 700 }}>
              {s.win_pct != null ? `${s.win_pct}%` : "—"}</div>
              <div className="l">WIN rate ({s.wins}/{s.n_closed} net-profitable) · {s.sum_r >= 0 ? "+" : ""}{s.sum_r}R</div></div>
            <div><div style={{ fontSize: 22, fontWeight: 700 }}>{s.open}</div>
              <div className="l">Open position{s.open === 1 ? "" : "s"}</div></div>
            {account !== "A" && (
              <div><div style={{ fontSize: 22, fontWeight: 700 }}>{s.skipped ?? 0}</div>
                <div className="l">
                  Skipped{account === "B" ? " (extended)" : ""}
                  {account === "C" && s.skipped_by && Object.keys(s.skipped_by).length > 0 && (
                    <> · {Object.entries(s.skipped_by).map(([k, n]) => `${k} ${n}`).join(" · ")}</>
                  )}
                </div></div>
            )}
            {account === "C" && (
              <div><div style={{ fontSize: 22, fontWeight: 700 }}>{s.risk_pct}%</div>
                <div className="l">Risk/trade · pre-registered, fixed for the month</div></div>
            )}
            {account !== "C" && (
            <div style={{ display: "flex", gap: 10, alignItems: "flex-end", marginLeft: "auto" }}>
              <label style={{ fontSize: 12 }}>Capital ₹<br />
                <input style={{ width: 110 }} value={capEdit}
                  onChange={(e) => setCapEdit(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>Risk %/trade<br />
                <input style={{ width: 70 }} value={riskEdit}
                  onChange={(e) => setRiskEdit(e.target.value)} /></label>
              <button className="secondary" disabled={saving || !capOk || !riskOk}
                onClick={saveAccount}>{saving ? "Saving…" : "Save"}</button>
            </div>
            )}
          </div>
          {(!capOk || !riskOk) && (
            <p className="muted" style={{ fontSize: 11, marginTop: 6 }}>
              Capital ≥ ₹10,000 · Risk 0.1–10%. Applies to NEW trades; equity re-bases on the new capital.
            </p>
          )}
        </div>
      )}

      <div className="card">
        <h3>Trades <span className="muted">— live paper fills, newest first (last 35 days)</span></h3>
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr><th>Day</th><th>Entry time</th><th>Exit time</th><th>Rule</th><th>Buy</th>
                <th>Entry ₹ (premium)</th><th>Exit ₹ (premium)</th>
                <th>Run-up</th>
                <th>Lots</th><th>Capital used</th><th>Charges ₹</th><th>P&L ₹ (net)</th>
                <th>Outcome</th><th>Equity</th></tr>
            </thead>
            <tbody>
              {rows.map((t: any) => (
                <tr key={t.id} style={t.status === "open" ? { background: "var(--bg-subtle, #f6f7f8)" } : undefined}>
                  <td className="mono">{t.day}</td>
                  <td className="mono">{t.entry_t}</td>
                  <td className="mono">{t.exit_t ?? "—"}</td>
                  <td><b>{t.rule}</b></td>
                  <td><b>{t.instrument}</b>
                    <div className="muted" style={{ fontSize: 11 }}>
                      {t.expiry ? `exp ${t.expiry}` : ""}{t.spot ? ` · spot ${t.spot}` : ""}</div></td>
                  <td className="mono">{inr(t.entry_p)}
                    <div className="muted" style={{ fontSize: 10 }}>SL {inr(t.sl)} · TP {inr(t.tp)}</div></td>
                  <td className="mono" style={{ color: t.exit_p == null ? undefined : t.exit_p >= t.entry_p ? "var(--green)" : "var(--red)" }}>
                    {t.exit_p != null ? inr(t.exit_p) : "—"}
                    {t.exit_source === "modeled" && <div className="muted" style={{ fontSize: 10 }}>modeled</div>}
                  </td>
                  <td className="mono" style={{ color: t.runup_pct != null && t.runup_pct > 15 ? "var(--red)" : undefined }}>
                    {t.runup_pct != null ? `${t.runup_pct}%` : "—"}</td>
                  <td>{t.status === "skipped" ? "—" : t.lots}</td>
                  <td className="mono">{inr(t.capital_used)}</td>
                  <td className="mono" style={{ color: "var(--red)" }}>{t.charges != null ? `−${Math.abs(t.charges).toLocaleString("en-IN")}` : "—"}</td>
                  <td className="mono" style={{ color: pnlColor(t.pnl) }}>
                    {t.pnl != null ? `${t.pnl >= 0 ? "+" : ""}${t.pnl.toLocaleString("en-IN")}` : "—"}</td>
                  <td>
                    {t.status === "open"
                      ? <>
                          <span className="pill" style={{ background: "#e8f0fe", color: "#1a56db" }}>OPEN</span>
                          <button className="secondary" disabled={exiting === t.id}
                            style={{ marginLeft: 6, padding: "2px 8px", fontSize: 11 }}
                            onClick={() => manualExit(t.id)}>
                            {exiting === t.id ? "…" : "Exit"}</button>
                        </>
                      : t.status === "skipped"
                      ? <span className="pill" style={{ background: "#f3f4f6", color: "#4b5563" }}
                          title={`Skipped — ${SKIP_WHY[t.outcome] ?? t.outcome}`}>SKIP·{t.outcome}</span>
                      : <span className="pill" style={{
                          background: t.outcome === "TP" ? "#def7ec" : t.outcome === "SL" ? "#fde8e8"
                            : t.outcome === "MANUAL" ? "#ede9fe" : "#fdf6b2",
                          color: t.outcome === "TP" ? "#03543f" : t.outcome === "SL" ? "#9b1c1c"
                            : t.outcome === "MANUAL" ? "#5b21b6" : "#723b13" }}>
                          {t.outcome}</span>}
                    {t.r != null && <span className="muted" style={{ fontSize: 11 }}> {t.r >= 0 ? "+" : ""}{t.r}R</span>}
                  </td>
                  <td className="mono">{t.equity_after != null ? inr(t.equity_after) : "—"}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={14} className="muted">
                  No paper trades yet — from the next market session, every real signal opens a row
                  here automatically and fills in as it resolves.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="disclaimer" style={{ padding: 0, marginTop: 8 }}>
          {s?.note}
        </p>
      </div>
    </div>
  );
}
