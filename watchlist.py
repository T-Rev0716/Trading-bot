"""The watchlist: discovered tokens kept until they can be judged or can never be.

Discovery only sees GeckoTerminal's newest pools, and on Solana those are minutes old,
younger than the 15-minute floor. Without a memory the desk would only ever see tokens
too young to trade: by the time one matured it had left the newest pages for good.

Every token FOMO returns, and that is not already too old, is watched. Each cycle the
watched tokens are re-fetched from FOMO alongside the newest discoveries, so they are
always evaluated on current data, never on what was seen when they were found.

    expiry     created_at + max token age (thresholds.HARD). A token with no known
               launch time expires max age after it was first seen.
    removal    expired, observed too old, or rejected for a reason that benches longer
               than max age (a permanent fact such as a honeypot)
    bound      at most max_size entries; when over, the earliest discovered is evicted
               (first_seen, then tid), once per batch
    batches    a scan applies all its updates in one transaction (apply_batch): drops,
               then refreshes and inserts, then capacity is enforced once. A token
               already watched keeps its first_seen however often it is seen, and
               nothing evicted in a batch can be re-inserted by that same batch.
    persists   in desk.db (DESK_DB), next to the bench, across restarts and resumes

The scan is not replayed: its output is journaled per cycle, so the watchlist changes
what the scan returns, never what replay does with it.
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist(
  tid TEXT PRIMARY KEY, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
  created_at REAL, expires_at REAL NOT NULL, times_seen INTEGER NOT NULL DEFAULT 1);
"""


class Watchlist:
    def __init__(self, db: sqlite3.Connection, max_age_hours: float, max_size: int,
                 lock=None):
        self.db, self.max_age_s, self.max_size = db, max_age_hours * 3600, max_size
        self.lock = lock
        db.executescript(SCHEMA)
        db.commit()

    def _q(self, sql, args=()):
        if self.lock:
            with self.lock:
                cur = self.db.execute(sql, args)
                self.db.commit()
                return cur.fetchall()
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur.fetchall()

    def prune(self, now: float) -> int:
        """Drop what has passed max age. Returns how many."""
        n = self._q("SELECT COUNT(*) FROM watchlist WHERE expires_at < ?", (now,))[0][0]
        self._q("DELETE FROM watchlist WHERE expires_at < ?", (now,))
        return n

    def active(self, now: float) -> list[str]:
        """Watched tokens not yet expired, earliest discovered first: a stable order."""
        return [r[0] for r in self._q(
            "SELECT tid FROM watchlist WHERE expires_at >= ? ORDER BY first_seen, tid",
            (now,))]

    def apply_batch(self, observed: list, drops, now: float) -> dict:
        """Apply one scan's watchlist changes atomically and report them.

        observed  [(tid, created_at or None), ...] every token the scan saw that may
                  still mature; repeats are merged (a known launch time wins)
        drops     tids to stop watching (seen too old, or permanently benched); a drop
                  wins over an observation of the same token in the batch

        Order: drops, then refresh existing entries (first_seen kept) and insert new
        ones (first_seen = now), then evict down to max_size by (first_seen, tid).
        Returns counts plus the tids removed, so 'operations' and 'unique tokens' can
        be reported apart."""
        drops = set(drops)
        merged: dict[str, float | None] = {}
        for tid, created in observed:
            if tid not in drops:
                merged[tid] = created if created is not None else merged.get(tid)
        lock = self.lock
        if lock:
            lock.acquire()
        try:
            with self.db:                                    # one transaction
                before = {r[0]: r[1:] for r in self.db.execute(
                    "SELECT tid, first_seen, created_at FROM watchlist")}
                dropped = sorted(t for t in drops if t in before)
                self.db.executemany("DELETE FROM watchlist WHERE tid=?",
                                    [(t,) for t in dropped])
                inserted, refreshed = [], []
                for tid in sorted(merged):
                    created = merged[tid]
                    if tid in before and tid not in drops:
                        first, known = before[tid]
                        created = created if created is not None else known
                        base = created if created is not None else first
                        self.db.execute(
                            "UPDATE watchlist SET last_seen=?, created_at=?, expires_at=?, "
                            "times_seen = times_seen + 1 WHERE tid=?",
                            (now, created, base + self.max_age_s, tid))
                        refreshed.append(tid)
                    else:
                        self.db.execute(
                            "INSERT INTO watchlist VALUES (?,?,?,?,?,1)",
                            (tid, now, now, created,
                             (created if created is not None else now) + self.max_age_s))
                        inserted.append(tid)
                over = self.db.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] \
                    - self.max_size
                evicted = []
                if over > 0:
                    evicted = [r[0] for r in self.db.execute(
                        "SELECT tid FROM watchlist ORDER BY first_seen, tid LIMIT ?",
                        (over,))]
                    self.db.executemany("DELETE FROM watchlist WHERE tid=?",
                                        [(t,) for t in evicted])
                after = {r[0] for r in self.db.execute("SELECT tid FROM watchlist")}
        finally:
            if lock:
                lock.release()
        new_evicted = set(evicted) & set(inserted)
        return {"inserted": len(inserted), "refreshed": len(refreshed),
                "dropped": len(dropped), "eviction_operations": len(evicted),
                "removed_unique": len(set(before) - after),
                "inserted_then_evicted": len(new_evicted), "size": len(after),
                "evicted_tids": evicted, "dropped_tids": dropped}

    def observe(self, tid: str, created_at: float | None, now: float) -> int:
        """One token, as a batch of one. Returns eviction operations."""
        return self.apply_batch([(tid, created_at)], (), now)["eviction_operations"]

    def drop(self, tid: str):
        self.apply_batch([], [tid], 0.0)

    def size(self) -> int:
        return self._q("SELECT COUNT(*) FROM watchlist")[0][0]

    def entry(self, tid: str) -> dict | None:
        cur = self.db.execute("SELECT * FROM watchlist WHERE tid=?", (tid,))
        row = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None
