"""One-time correction (user-approved 16-Sep-2026): today's two 'modeled'
exits, now verifiable against the option's REAL Kite minute candles.

Audit result: outcomes (TIME) were correct on both; the delta-mapped exit
premiums were optimistic. Rewrite exit_p to the real 20-minute closes —
NIFTY 23250 CE 160.52→155.35, BANKNIFTY 56400 CE 633.90→625.00 — on both
accounts (A+B), keep exit_source='modeled' (still a reconstructed fill, and
the 0.25%/side slippage convention stays), then re-chain equity_after.
"""
from app.backtest import trade_cost
from app.db import SessionLocal
from app.models import ScalpPaperTrade as T

REAL = {("NIFTY 23250 CE", "2026-09-16", 157.4): 155.35,
        ("BANKNIFTY 56400 CE", "2026-09-16", 620.5): 625.00}


def main() -> None:
    with SessionLocal() as db:
        fixed = []
        for (inst, day, ep), real_exit in REAL.items():
            rows = (db.query(T).filter(T.instrument == inst, T.day == day,
                                       T.entry_p == ep, T.exit_source == "modeled",
                                       T.status == "closed").all())
            for t in rows:
                before = (t.account, t.exit_p, t.r_multiple, t.pnl)
                risk = max(t.entry_p - t.sl, 0.01)
                t.exit_p = real_exit
                t.r_multiple = round((real_exit - t.entry_p) / risk, 2)
                t.charges = trade_cost(t.entry_p, real_exit, t.lot_size, t.lots,
                                       slip_pct=0.25)
                t.pnl = round((real_exit - t.entry_p) * t.lot_size * t.lots
                              - t.charges, 2)
                t.equity_after = None  # re-chained below
                fixed.append((before, (t.account, t.exit_p, t.r_multiple, t.pnl)))
        db.flush()
        # re-chain equity_after for today's corrected rows, in exit order
        from app.paper_trade import _base_capital
        import json

        from app.models import Setting

        rows = (db.query(T).filter(T.day == "2026-09-16", T.status == "closed")
                .order_by(T.exit_at).all())
        for t in rows:
            srow = db.get(Setting, t.user_id)
            cfg = json.loads(srow.config_json or "{}") if srow else {}
            closed_pnl = sum(x.pnl or 0.0 for x in db.query(T)
                             .filter(T.user_id == t.user_id, T.account == t.account,
                                     T.status == "closed", T.exit_at <= t.exit_at).all())
            t.equity_after = round(_base_capital(cfg) + closed_pnl, 2)
        db.commit()
        for before, after in fixed:
            print("BEFORE", before, "→ AFTER", after)


if __name__ == "__main__":
    main()
