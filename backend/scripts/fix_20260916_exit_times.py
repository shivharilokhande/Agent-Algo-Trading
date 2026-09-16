"""One-time repair (16-Sep): modeled rows stamped the delayed sweep's clock as
exit_at. The rule exited at entry+20min — set exit_at to that moment.
NIFTY 23250 CE 11:55 → exit 12:15 IST; BANKNIFTY 56400 CE 12:39 → 12:59 IST.
Prices/P&L untouched (already corrected to real candle closes)."""
from datetime import timedelta

from app.db import SessionLocal
from app.models import ScalpPaperTrade as T


def main() -> None:
    with SessionLocal() as db:
        rows = (db.query(T).filter(T.day == "2026-09-16",
                                   T.exit_source == "modeled",
                                   T.status == "closed").all())
        for t in rows:
            old = t.exit_at
            t.exit_at = t.created_at + timedelta(minutes=20)
            print(t.account, t.instrument, "exit_at", old, "→", t.exit_at)
        db.commit()


if __name__ == "__main__":
    main()
