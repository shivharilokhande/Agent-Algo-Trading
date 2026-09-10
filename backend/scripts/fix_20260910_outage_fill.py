"""One-time correction (user-approved 10-Sep-2026): the 11:42 ORB PE trade.

A network outage (api.kite.trade DNS failures + timeouts ~12:02-12:22 IST)
froze the paper position through its 20-minute time stop; the first quote
back (₹179.2, 12:22) was stamped as a TP fill. Rule-faithful result from
the standard spot-replay fallback: TIME exit at 12:02 → this script rewrites
the row to that result, marked exit_source='modeled'.
"""
from datetime import timezone

from app.backtest import _simulate_trade, trade_cost
from app.db import SessionLocal
from app.models import ScalpPaperTrade as T
from app.paper_score import _bar_index_at
from app.paper_trade import _base_capital
from app.scalp import IST, fetch_session_bars


def main() -> None:
    import json

    from app.models import Setting

    with SessionLocal() as db:
        t = db.query(T).filter(T.day == "2026-09-10", T.outcome == "TP").one()
        print("BEFORE:", t.exit_p, t.outcome, t.r_multiple, t.pnl,
              t.equity_after, t.exit_source)
        bars = fetch_session_bars(t.symbol)
        i = _bar_index_at(bars, t.created_at.replace(tzinfo=timezone.utc).astimezone(IST))
        res = _simulate_trade(bars, i, t.direction, t.entry_p,
                              delta=abs(t.delta) if t.delta else None)
        assert res["outcome"] == "TIME", res
        risk = t.entry_p - t.sl
        exit_p = round(t.entry_p + res["r"] * risk, 2)
        t.exit_p = exit_p
        t.outcome = res["outcome"]
        t.r_multiple = round(res["r"], 2)
        t.exit_source = "modeled"
        t.charges = trade_cost(t.entry_p, exit_p, t.lot_size, t.lots, slip_pct=0.25)
        t.pnl = round((exit_p - t.entry_p) * t.lot_size * t.lots - t.charges, 2)
        db.flush()
        srow = db.get(Setting, t.user_id)
        base = _base_capital(json.loads(srow.config_json or "{}") if srow else {})
        closed_pnl = sum(x.pnl or 0.0 for x in db.query(T)
                         .filter(T.user_id == t.user_id, T.account == "A",
                                 T.status == "closed").all())
        t.equity_after = round(base + closed_pnl, 2)
        db.commit()
        print("AFTER:", t.exit_p, t.outcome, t.r_multiple, t.pnl,
              t.equity_after, t.exit_source)


if __name__ == "__main__":
    main()
