import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

export default function Portfolio() {
  const [snap, setSnap] = useState<any>(null);
  const [settings, setSettings] = useState<any>({});
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [busy, setBusy] = useState("");

  async function load() {
    setSnap(await api.get("/api/portfolio"));
    setSettings((await api.get<{ config: any }>("/api/settings")).config || {});
  }
  useEffect(() => { load(); }, []);

  async function toggle() {
    const next = { ...settings, paper_trading: !settings.paper_trading };
    if (!next.paper_notional) next.paper_notional = 10000;
    await api.put("/api/settings", { config: next });
    setSettings(next);
    setMsg({
      kind: "ok",
      text: next.paper_trading
        ? "Paper trading ON — Buy/Overweight decisions open simulated positions; Underweight/Sell close them. Real prices, simulated fills, no broker orders."
        : "Paper trading OFF — future decisions won't touch the paper book.",
    });
  }

  async function close(id: string) {
    setBusy(id);
    try {
      const r = await api.post<any>(`/api/portfolio/${id}/close`);
      setMsg({ kind: "ok", text: `Closed at $${r.exit_price} — realized P&L $${r.realized_pnl}` });
      await load();
    } catch (ex: any) {
      setMsg({ kind: "error", text: ex.message });
    } finally { setBusy(""); }
  }

  if (!snap) return <p className="muted">Loading…</p>;
  const pnl = (v: number | null) =>
    v === null || v === undefined ? "—" : (
      <span style={{ color: v >= 0 ? "var(--green)" : "var(--red)" }} className="mono">
        {v >= 0 ? "+" : ""}{v.toFixed(2)}
      </span>
    );

  const open = snap.positions.filter((p: any) => p.status === "open");
  const closed = snap.positions.filter((p: any) => p.status === "closed");

  return (
    <div>
      <h1>Paper Portfolio</h1>
      <p className="page-sub">Simulated fills at real market prices — decisions execute here automatically when paper trading is on. No real broker orders are ever placed.</p>
      {msg && <div className={msg.kind === "ok" ? "ok-box" : "error-box"}>{msg.text}</div>}

      <div className="statrow four">
        <div className="statcard">
          <div className="k">Paper trading</div>
          <div className="v" style={{ fontSize: 18, marginTop: 12 }}>
            <span className={`pill ${settings.paper_trading ? "done" : "pending"}`}>
              {settings.paper_trading ? "enabled" : "off"}
            </span>
          </div>
          <div className="s"><a href="#" onClick={(e) => { e.preventDefault(); toggle(); }}>
            {settings.paper_trading ? "turn off" : "turn on"}</a> · unit ${settings.paper_notional ?? 10000}</div>
        </div>
        <div className="statcard"><div className="k">Open positions</div><div className="v">{snap.open_count}</div><div className="s">auto-managed by decisions</div></div>
        <div className="statcard"><div className="k">Unrealized P&L</div>
          <div className={`v ${snap.unrealized_pnl >= 0 ? "green" : "red"}`}>${snap.unrealized_pnl}</div>
          <div className="s">at live prices</div></div>
        <div className="statcard"><div className="k">Realized P&L</div>
          <div className={`v ${snap.realized_pnl >= 0 ? "green" : "red"}`}>${snap.realized_pnl}</div>
          <div className="s">closed positions</div></div>
      </div>

      <div className="card">
        <h3>Open positions</h3>
        <table>
          <thead><tr><th>Ticker</th><th>Opened by</th><th>Qty</th><th>Entry</th><th>Last</th><th>Unrealized</th><th>Opened</th><th></th></tr></thead>
          <tbody>
            {open.map((p: any) => (
              <tr key={p.id}>
                <td><b>{p.ticker}</b></td>
                <td><span className={`rating ${p.rating}`}>{p.rating}</span>{" "}
                  {p.run_id && <Link to={`/runs/${p.run_id}`} className="muted">run</Link>}</td>
                <td className="mono">{p.qty}</td>
                <td className="mono">${p.entry_price}</td>
                <td className="mono">{p.last_price ? `$${p.last_price}` : "—"}</td>
                <td>{pnl(p.unrealized_pnl)}</td>
                <td className="muted">{new Date(p.opened_at).toLocaleDateString()}</td>
                <td><button className="danger small" disabled={busy === p.id} onClick={() => close(p.id)}>
                  {busy === p.id ? "…" : "Close"}</button></td>
              </tr>
            ))}
            {open.length === 0 && <tr><td colSpan={8} className="muted">
              No open positions. Turn paper trading on and run an analysis — a Buy/Overweight decision opens one automatically.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Closed positions</h3>
        <table>
          <thead><tr><th>Ticker</th><th>Entry → Exit</th><th>Realized P&L</th><th>Reason</th><th>Closed</th></tr></thead>
          <tbody>
            {closed.map((p: any) => (
              <tr key={p.id}>
                <td><b>{p.ticker}</b></td>
                <td className="mono">${p.entry_price} → ${p.exit_price}</td>
                <td>{pnl(p.realized_pnl)}</td>
                <td className="muted">{p.close_reason}</td>
                <td className="muted">{p.closed_at ? new Date(p.closed_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
            {closed.length === 0 && <tr><td colSpan={5} className="muted">Nothing closed yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
