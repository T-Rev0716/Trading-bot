"""What the desk remembers between cycles: the open position and the bench.

Creates desk.db on first run. DESK_DB overrides the path.
"""
import os
import sqlite3
import sys
import threading
import time

DB = sqlite3.connect(os.environ.get("DESK_DB", "desk.db"), check_same_thread=False)
DB.executescript("""
CREATE TABLE IF NOT EXISTS position(
  id INTEGER PRIMARY KEY CHECK (id = 1),
  ticker TEXT, addr TEXT, net INT, opened_at REAL);
CREATE TABLE IF NOT EXISTS bench(
  tid TEXT PRIMARY KEY, reason TEXT, until REAL);
""")
_lock = threading.Lock()                # judge.py serves /book/release from threads

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
    "trades": 25, "mcap": 25, "age": 20, "dossier_failed": 30,
}
DEFAULT_BENCH = 45


def held():
    with _lock:
        r = DB.execute("SELECT ticker, opened_at FROM position WHERE id=1").fetchone()
    return {"ticker": r[0], "minutes": (time.time() - r[1]) / 60} if r else None


def take(order):
    t = order["token"]
    with _lock:
        DB.execute("INSERT OR REPLACE INTO position VALUES (1,?,?,?,?)",
                   (t["ticker"], t["address"], t["network_id"], time.time()))
        DB.commit()


def release():
    """RISK calls this the moment a close is filled. Nothing else calls it."""
    with _lock:
        DB.execute("DELETE FROM position")
        DB.commit()


def benched(tid: str) -> bool:
    with _lock:
        r = DB.execute("SELECT until FROM bench WHERE tid=?", (tid,)).fetchone()
    return bool(r and r[0] > time.time())


def sit(tid: str, reason: str):
    mins = BENCH_MINUTES.get(reason, DEFAULT_BENCH)
    with _lock:
        DB.execute("INSERT OR REPLACE INTO bench VALUES (?,?,?)",
                   (tid, reason, time.time() + mins * 60))
        DB.execute("DELETE FROM bench WHERE until < ?", (time.time(),))
        DB.commit()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "held"
    if cmd == "release":
        release()
    print(held())
