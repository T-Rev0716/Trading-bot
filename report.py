"""Performance of each paper ledger, side by side.

    python report.py                 reads PAPER_DB (default paper.db)
"""
import json
import os
import sys
from collections import Counter

from ledger import Ledger, connect


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
        "realized_pnl": round(realized, 2), "unrealized_pnl": round(unrealized, 2),
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
    db = connect(os.environ.get("PAPER_DB", "paper.db"))
    names = [r[0] for r in db.execute("SELECT ledger FROM accounts ORDER BY ledger")]
    if not names:
        print("no paper ledgers yet")
        sys.exit(0)
    print(compare([metrics(Ledger(db, n)) for n in names]))
