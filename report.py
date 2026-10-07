"""Performance of each paper ledger, side by side.

    python report.py runs/<run_id>/paper.db

Three kinds of number, kept apart:
  observed     fills priced from a quote that was actually received
  assumed      blind closes: no quote, so valued at last price minus the haircut in
               thresholds.EXITS. That value is an assumption, not an observation.
  stress       zero recovery: every blind close and every open position is worth $0,
               as if the token turned out to be unsellable.

All of it is simulated. Paper fills are not market evidence.
"""
import json
import os
import sys
from collections import Counter

from ledger import Ledger, connect
from thresholds import EXITS


def _blind(db, p) -> bool:
    (f,) = db.execute("SELECT flags FROM orders WHERE order_id=?",
                      (p["exit_order_id"],)).fetchone()
    return "blind_close" in json.loads(f or "[]")


def metrics(ledger: Ledger) -> dict:
    db, name = ledger.db, ledger.name
    closed = ledger.positions("CLOSED")
    held = ledger.positions("OPEN", "CLOSING")
    start, cash, equity = ledger.starting_cash(), ledger.cash(), ledger.equity()

    realized = sum(p["realized_pnl"] for p in closed)
    unrealized = sum(p["qty"] * (p["last_price"] or 0) - p["entry_notional"] - p["entry_fee"]
                     for p in held)
    fees = db.execute("SELECT COALESCE(SUM(fee_usd),0) FROM orders WHERE ledger=? AND "
                      "state='FILLED'", (name,)).fetchone()[0]
    slip = db.execute("SELECT COALESCE(SUM(fill_notional * slippage_bps / 10000.0),0), "
                      "AVG(slippage_bps) FROM orders WHERE ledger=? AND state='FILLED'",
                      (name,)).fetchone()
    returns = [p["realized_pnl"] / (p["entry_notional"] + p["entry_fee"]) for p in closed]
    wins = [r for r in returns if r > 0]

    peak, mdd = None, 0.0
    for (eq,) in db.execute("SELECT equity FROM marks WHERE ledger=? ORDER BY ts, rowid",
                            (name,)):
        peak = eq if peak is None else max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)

    blind = [p for p in closed if _blind(db, p)]
    seen = [p for p in closed if not _blind(db, p)]
    assumed_proceeds = sum(p["exit_notional"] - p["exit_fee"] for p in blind)
    open_value = sum(p["qty"] * (p["last_price"] or 0) for p in held)
    stress_equity = equity - assumed_proceeds - open_value

    states = Counter(r[0] for r in db.execute("SELECT state FROM orders WHERE ledger=?",
                                              (name,)))
    flags = Counter()
    for (f,) in db.execute("SELECT flags FROM orders WHERE ledger=? AND flags IS NOT NULL",
                           (name,)):
        flags.update(json.loads(f))
    return {
        "ledger": name,
        "starting_cash": round(start, 2), "cash": round(cash, 2),
        "reserved_for_unsettled": round(ledger.reserved(), 2),
        "equity": round(equity, 2), "return_pct": round((equity / start - 1) * 100, 3),
        "realized_pnl": round(realized, 2),
        "realized_pnl_observed": round(sum(p["realized_pnl"] for p in seen), 2),
        "realized_pnl_assumed": round(sum(p["realized_pnl"] for p in blind), 2),
        "blind_closes": len(blind),
        "blind_close_assumed_proceeds": round(assumed_proceeds, 2),
        "blind_close_haircut_assumed": EXITS["stale_quote_haircut"],
        "unrealized_pnl": round(unrealized, 2),
        "stress_zero_recovery_equity": round(stress_equity, 2),
        "stress_zero_recovery_return_pct": round((stress_equity / start - 1) * 100, 3),
        "fees_paid": round(fees, 2), "est_slippage_cost": round(slip[0], 2),
        "avg_slippage_bps": round(slip[1], 1) if slip[1] is not None else None,
        "closed_trades": len(closed), "open_positions": len(held),
        "win_rate": round(len(wins) / len(returns), 3) if returns else None,
        "avg_trade_return_pct": round(sum(returns) / len(returns) * 100, 3) if returns else None,
        "best_trade_pct": round(max(returns) * 100, 3) if returns else None,
        "worst_trade_pct": round(min(returns) * 100, 3) if returns else None,
        "max_drawdown_pct": round(mdd * 100, 3),
        "exit_reasons": dict(Counter(p["exit_reason"] for p in closed)),
        "orders_by_state": dict(states),
        "unknown_orders": states.get("UNKNOWN", 0),
        "orders_ever_unknown": db.execute(
            "SELECT COUNT(DISTINCT e.order_id) FROM order_events e JOIN orders o "
            "ON o.order_id = e.order_id WHERE o.ledger=? AND e.to_state='UNKNOWN'",
            (name,)).fetchone()[0],
        "fill_flags": dict(flags),
    }


def compare(rows: list[dict]) -> str:
    keys = [k for k in rows[0] if k != "ledger"]
    w = max(len(k) for k in keys)
    cols = [r["ledger"] for r in rows]
    out = [f"{'':{w}}  " + "  ".join(f"{c:>22}" for c in cols)]
    for k in keys:
        out.append(f"{k:{w}}  " + "  ".join(f"{str(r[k]):>22}" for r in rows))
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python report.py runs/<run_id>")
    path = sys.argv[1]
    if os.path.isdir(path):
        path = os.path.join(path, "paper.db")
    db = connect(path)
    names = [r[0] for r in db.execute("SELECT ledger FROM accounts ORDER BY ledger")]
    if not names:
        print("no paper ledgers yet")
        sys.exit(0)
    print("SIMULATED PAPER RESULTS. Not market evidence.\n")
    print(compare([metrics(Ledger(db, n)) for n in names]))
