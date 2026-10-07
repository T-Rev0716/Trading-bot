"""The bench: a rejected token stays rejected for a while, by what rejected it.

Positions and orders live in the paper ledger (ledger.py), not here.

Scopes keep the strategy-versus-baseline comparison fair:
  shared     a fact or a data rule both ledgers apply. Nobody sees the token.
  strategy   a judge answer rejected it. The baseline, which never asks, still can.

Creates desk.db on first run. DESK_DB overrides the path.
"""
import os
import sqlite3
import sys
import threading
import time

DB = sqlite3.connect(os.environ.get("DESK_DB", "desk.db"), check_same_thread=False)
DB.executescript("""
CREATE TABLE IF NOT EXISTS bench_v2(
  tid TEXT, scope TEXT CHECK (scope IN ('shared','strategy')), reason TEXT, until REAL,
  PRIMARY KEY (tid, scope));
""")
_lock = threading.Lock()

# how long a rejection stands, by what fired it
BENCH_MINUTES = {
    # facts that will not change while this token exists
    "honeypot": 100_000, "authority_open": 100_000,
    "top_wallet": 100_000, "sell_side": 100_000,
    # slow to change
    "recycled_account": 360, "account_is_the_project": 360,
    # can change as the float moves
    "top_10": 90, "holders": 90, "dev_still_loaded": 90,
    "concentration_is_exit_risk": 90,
    # can change inside the hour, keep it short or you miss the token maturing
    "shape": 25, "shape_weak": 25, "momentum_already_spent": 25,
    "liquidity_fits_ticket": 25, "liquidity": 25, "volume": 25,
    "trades": 25, "mcap": 25, "dossier_failed": 30,
    # age: a token only gets older. Too old is final; too young is never benched (it is
    # watched until it matures, see watchlist.py); missing may be filled in later.
    "age_too_old": 100_000, "age_missing": 20, "age_too_young": 0,
}
DEFAULT_BENCH = 45


def benched(tid: str, now: float | None = None) -> str | None:
    """'shared', 'strategy', or None when the token is free to look at."""
    now = time.time() if now is None else now
    with _lock:
        rows = DB.execute("SELECT scope FROM bench_v2 WHERE tid=? AND until > ?",
                          (tid, now)).fetchall()
    scopes = {r[0] for r in rows}
    return "shared" if "shared" in scopes else ("strategy" if scopes else None)


def bench_minutes(reason: str) -> float:
    return BENCH_MINUTES.get(reason.split(":")[0], DEFAULT_BENCH)


def sit(tid: str, reason: str, scope: str = "shared", now: float | None = None):
    now = time.time() if now is None else now
    with _lock:
        DB.execute("INSERT OR REPLACE INTO bench_v2 VALUES (?,?,?,?)",
                   (tid, scope, reason, now + bench_minutes(reason) * 60))
        DB.execute("DELETE FROM bench_v2 WHERE until < ?", (now,))
        DB.commit()


if __name__ == "__main__":
    for row in DB.execute("SELECT tid, scope, reason, until FROM bench_v2 WHERE until > ? "
                          "ORDER BY until", (time.time(),)):
        print(row[0], row[1], row[2], f"{(row[3] - time.time()) / 60:.0f} min left")
    sys.exit(0)
