"""Paper-week scoreboard — score REAL scalp signals against actual candles.

The go-live gate for one-tap execution is evidence, not vibes: every real
(non-simulated) signal gets replayed after the close against the session's
actual 1m bars under the live exit policy A (SL −1R / TP +1.5R / 20-min time
stop), using the signal's REAL quoted entry premium. Results are stored on the
signal itself (payload_json["paper"]) so the scoreboard is reproducible.

WALL_REJECT is reported separately — it is the one rule the backtest cannot
audit (no historical OI), so the paper week is its only evidence.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger("agentalgo.paper_score")

SCORE_AFTER_HM = (15, 35)  # session is done; candles for the day are final
TREND_RULES = ("ORB", "VWAP_RECLAIM")


def _created_ist(created_at: datetime) -> datetime:
    """Signal rows store naive UTC — convert to IST."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(IST)


def _bar_hm(b: dict) -> tuple[int, int]:
    """(hour, minute) of a bar — live bars carry only 't', history bars 'hm'."""
    if "hm" in b:
        return tuple(b["hm"])  # type: ignore[return-value]
    t = datetime.fromisoformat(b["t"])
    return (t.hour, t.minute)


def _bar_index_at(bars: list[dict], created: datetime) -> int | None:
    """Entry bar: the last COMPLETED bar strictly before the signal minute.

    R6-5: live signals fire off completed bars through minute−1, so the bar
    labeled with the signal minute is post-signal data — entering on its close
    (30–60s after emission) and skipping it in the scan hid same-minute SL/TP
    touches. Strict `<` makes the replay start scanning FROM the signal minute.
    """
    hm = (created.hour, created.minute)
    idx = None
    for i, b in enumerate(bars):
        if _bar_hm(b) < hm:
            idx = i
        else:
            break
    return idx


def score_signal(payload: dict, direction: str, bars: list[dict],
                 created: datetime) -> dict | None:
    """Replay one signal on actual bars. None = can't score (no bars after)."""
    from .backtest import _simulate_trade

    i = _bar_index_at(bars, created)
    if i is None or i >= len(bars) - 1:
        return None
    ep = float(payload.get("ep") or 0)
    if not ep:
        return None
    delta = payload.get("delta")  # real strike delta when the ladder had it
    res = _simulate_trade(bars, i, direction, ep,
                          delta=float(delta) if delta else None)
    return {"outcome": res["outcome"], "r": res["r"], "exit_t": res["exit_t"],
            "bars_held": res["bars_held"],
            "scored_at": datetime.now(IST).isoformat(timespec="seconds")}


def score_day(day_iso: str | None = None, user_id: str | None = None) -> int:
    """Score all real, unscored signals for one IST day. Returns count scored."""
    from .backtest import fetch_history_sessions
    from .db import SessionLocal
    from .models import ScalpSignal

    day_iso = day_iso or datetime.now(IST).date().isoformat()
    with SessionLocal() as db:
        q = db.query(ScalpSignal).filter(ScalpSignal.simulated.is_(False))
        if user_id:
            q = q.filter(ScalpSignal.user_id == user_id)
        rows = [r for r in q.all()
                if _created_ist(r.created_at).date().isoformat() == day_iso]
        todo = []
        for r in rows:
            p = json.loads(r.payload_json or "{}")
            if "paper" not in p:
                todo.append((r, p))
        if not todo:
            return 0
        bars_by_symbol: dict[str, list[dict]] = {}
        scored = 0
        for r, p in todo:
            sym = r.symbol
            if sym not in bars_by_symbol:
                try:
                    sessions = fetch_history_sessions(sym, days=8)
                    bars_by_symbol[sym] = sessions.get(day_iso, [])
                except Exception as exc:  # noqa: BLE001
                    log.info("Paper score: no bars for %s: %s", sym, exc)
                    bars_by_symbol[sym] = []
            bars = bars_by_symbol[sym]
            if not bars:
                continue
            result = score_signal(p, r.direction, bars, _created_ist(r.created_at))
            if result is None:
                continue
            p["paper"] = result
            r.payload_json = json.dumps(p)
            scored += 1
        db.commit()
    if scored:
        log.info("Paper score: %s signal(s) scored for %s", scored, day_iso)
    return scored


def paper_summary(user_id: str, days: int = 14) -> dict:
    """Per-day + cumulative scoreboard of scored real signals for a user."""
    from .db import SessionLocal
    from .models import ScalpSignal

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days + 1)
    with SessionLocal() as db:
        rows = (db.query(ScalpSignal)
                .filter(ScalpSignal.user_id == user_id,
                        ScalpSignal.simulated.is_(False),
                        ScalpSignal.created_at >= cutoff)
                .order_by(ScalpSignal.created_at.asc())
                .all())
        parsed = [(r, json.loads(r.payload_json or "{}")) for r in rows]

    by_day: dict[str, dict] = {}
    by_rule: dict[str, dict] = {}
    unscored = 0
    for r, p in parsed:
        day = _created_ist(r.created_at).date().isoformat()
        score = p.get("paper")
        if not score:
            unscored += 1
            continue
        d = by_day.setdefault(day, {"day": day, "n": 0, "wins": 0, "sum_r": 0.0,
                                    "signals": []})
        rr = float(score["r"])
        d["n"] += 1
        d["wins"] += 1 if rr > 0 else 0
        d["sum_r"] = round(d["sum_r"] + rr, 2)
        d["signals"].append({"time": _created_ist(r.created_at).strftime("%H:%M"),
                             "symbol": r.symbol, "rule": r.rule,
                             "instrument": r.instrument,
                             "outcome": score["outcome"], "r": rr})
        g = by_rule.setdefault(r.rule, {"rule": r.rule, "n": 0, "wins": 0, "sum_r": 0.0})
        g["n"] += 1
        g["wins"] += 1 if rr > 0 else 0
        g["sum_r"] = round(g["sum_r"] + rr, 2)

    days_list = sorted(by_day.values(), key=lambda d: d["day"], reverse=True)
    n = sum(d["n"] for d in days_list)
    wins = sum(d["wins"] for d in days_list)
    sum_r = round(sum(d["sum_r"] for d in days_list), 2)
    trend = {"n": 0, "wins": 0, "sum_r": 0.0}
    for g in by_rule.values():
        if g["rule"] in TREND_RULES:
            trend["n"] += g["n"]
            trend["wins"] += g["wins"]
            trend["sum_r"] = round(trend["sum_r"] + g["sum_r"], 2)

    def _pct(w: int, t: int) -> float | None:
        return round(w / t * 100, 1) if t else None

    return {
        "days": days_list,
        "by_rule": sorted(by_rule.values(), key=lambda g: -g["n"]),
        "total": {"n": n, "wins": wins, "win_pct": _pct(wins, n), "sum_r": sum_r,
                  "expectancy_r": round(sum_r / n, 3) if n else None},
        "trend_rules": {**trend, "win_pct": _pct(trend["wins"], trend["n"])},
        "wall_reject": next((g | {"win_pct": _pct(g["wins"], g["n"])}
                             for g in by_rule.values() if g["rule"] == "WALL_REJECT"),
                            None),
        "unscored": unscored,
        "note": ("Real signals replayed on actual candles under exit policy A. "
                 "WALL_REJECT has no backtest history — judge it here only."),
    }


def live_day_sl_count(user_id: str, bars_by_symbol: dict[str, list[dict]]) -> int:
    """How many of TODAY's real signals have already hit their SL (intraday).

    Replays each real signal against the bars available so far (same math as
    the after-close scorer). Unresolved / TIME / TP outcomes don't count —
    only confirmed SLs. Feeds the 2-SL day stop.
    """
    from .db import SessionLocal
    from .models import ScalpSignal

    today = datetime.now(IST).date().isoformat()
    with SessionLocal() as db:
        rows = (db.query(ScalpSignal)
                .filter(ScalpSignal.user_id == user_id,
                        ScalpSignal.simulated.is_(False))
                .all())
        sigs = [(r, json.loads(r.payload_json or "{}")) for r in rows
                if _created_ist(r.created_at).date().isoformat() == today]
    count = 0
    fallback: dict[str, list[dict]] = {}
    for r, p in sigs:
        bars = bars_by_symbol.get(r.symbol) or []
        if not bars:
            # R6-10: don't fail open — a signal on a symbol whose live feed is
            # stale (or since-disabled) still counts toward the day stop; pull
            # its bars from the history path once per symbol.
            if r.symbol not in fallback:
                try:
                    from .backtest import fetch_history_sessions

                    fallback[r.symbol] = fetch_history_sessions(r.symbol, 2).get(today, [])
                except Exception:  # noqa: BLE001
                    log.warning("Day stop: no bars for %s — its SLs are UNCOUNTED", r.symbol)
                    fallback[r.symbol] = []
            bars = fallback[r.symbol]
            if not bars:
                continue
        res = score_signal(p, r.direction, bars, _created_ist(r.created_at))
        if res and res["outcome"] == "SL":
            count += 1
    return count


_last_scored_day: str | None = None


async def maybe_score_after_close() -> None:
    """Called from the scalp loop: once per weekday after 15:35 IST, score today."""
    global _last_scored_day
    import asyncio

    now = datetime.now(IST)
    today = now.date().isoformat()
    if (now.weekday() >= 5 or (now.hour, now.minute) < SCORE_AFTER_HM
            or _last_scored_day == today):
        return
    _last_scored_day = today  # set first — never retry-loop on a bad day
    try:
        n = await asyncio.to_thread(score_day, today)
        if n:
            await asyncio.to_thread(_emit_scorecards, today)
    except Exception:  # noqa: BLE001
        log.exception("Paper scoring failed for %s", today)


def _emit_scorecards(day_iso: str) -> None:
    """One alert per user with the day's paper result."""
    from .db import SessionLocal
    from .models import Alert, ScalpSignal

    with SessionLocal() as db:
        rows = db.query(ScalpSignal).filter(ScalpSignal.simulated.is_(False)).all()
        per_user: dict[str, list] = {}
        for r in rows:
            if _created_ist(r.created_at).date().isoformat() != day_iso:
                continue
            score = json.loads(r.payload_json or "{}").get("paper")
            if score:
                per_user.setdefault(r.user_id, []).append(float(score["r"]))
        for uid, rs in per_user.items():
            wins = sum(1 for x in rs if x > 0)
            db.add(Alert(user_id=uid, ticker="SCALP", type="scalp",
                         message=(f"PAPER SCORE {day_iso}: {len(rs)} signal(s), "
                                  f"{wins} profitable, net {sum(rs):+.2f}R")))
        db.commit()
